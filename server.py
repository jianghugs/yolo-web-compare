# -*- coding: utf-8 -*-
"""
行人检测 Web 控制台（端口 1239）—— 只做 YOLO 检测
  GET  /            页面
  POST /api/direct  单张图片 YOLO 检测（同步返回标注图+人数+耗时，不落盘、不回调）
  GET  /api/rtmp/snapshot  从 RTMP 实时流抓当前帧，返回 JPEG（页面轮询实现伪直播）
  GET  /api/rtmp/status     ffprobe 探测流状态（在线/编码/分辨率/音频）
  POST /api/rtmp/detect     抓当前帧并用 YOLO 实时检测（单次）
  POST /api/rtmp/autodetect/start|stop   连续检测开关（后台线程，检出人自动落盘）
  GET  /api/rtmp/autodetect/state   连续检测实时状态
  GET  /api/rtmp/gallery         检测图库列表（分页，默认每页 10 张）
  GET  /detections/<file>        查看检测图片
  GET  /api/health               健康检查

v1.3.0 优化：
  * 常驻 ffmpeg 拉流进程（StreamGrabber）：只取最新帧，帧率从 ~1fps 提到 5~15fps，
    断线指数退避重连（1s→30s），不再每帧新建连接
  * 全局推理锁：HTTP 线程与后台连续检测线程不再并发压同一个模型
  * 图库保存去重：检出画面与上一张 dHash 相似时不重复落盘，避免静置画面刷屏
  * 可选远程推理 WC_DETECT_VIA=http://127.0.0.1:1238：调用 1238 的
    /api/detectPreview 复用 GPU，本进程不加载模型（省一半显存）
  * 可选告警推送 WC_ALERT_WEBHOOK：检出人时 POST JSON 通知（可带标注图）
  * 图库文件名 HTML 转义，防注入；BASEDIR 用 os.path 兼容 Windows

启动：/home/deployer/miniconda3/envs/yolo11/bin/python server.py   （见 start.sh）
"""
import base64
import json
import os
import subprocess
import threading
import time
import urllib.request
from collections import deque

import cv2
import numpy as np
from flask import Flask, request, jsonify, send_from_directory, Response

import utils

CONF_THR = float(os.environ.get('WC_CONF', '0.5'))
PORT = int(os.environ.get('WC_PORT', '1239'))
VERSION = '1.3.0'

# RTMP 实时流（可在页面上改，这里只是默认值）
RTMP_URL = os.environ.get('WC_RTMP_URL', 'rtmp://<RTMP_HOST>:1935/live/<STREAM_KEY>')

# 可选：把推理转发给 1238 的 /api/detectPreview（省显存），留空 = 本进程加载模型
DETECT_VIA = os.environ.get('WC_DETECT_VIA', '').strip().rstrip('/')

# 可选：检出人时推送 webhook（JSON: count/ts/file[/image]）
ALERT_WEBHOOK = os.environ.get('WC_ALERT_WEBHOOK', '').strip()
ALERT_IMAGE = os.environ.get('WC_ALERT_IMAGE', '0') in ('1', 'true', 'yes')

BASEDIR = os.path.dirname(os.path.abspath(__file__))

app = Flask(__name__, static_folder=None)

# ---- 模型：本地加载，或转发给 1238 ----
if DETECT_VIA:
    DEVICE = 'remote:%s' % DETECT_VIA
    MODEL = None
    print('[compare] remote inference via %s (model not loaded here)' % DETECT_VIA, flush=True)
else:
    import torch
    from ultralytics import YOLO
    DEVICE = 'cuda:0' if torch.cuda.is_available() else 'cpu'
    print(f'[compare] loading YOLO on {DEVICE} ...', flush=True)
    MODEL = YOLO(os.path.join(BASEDIR, 'yolo11n.pt'))
    if DEVICE == 'cuda:0':
        MODEL.to(DEVICE)
    print('[compare] model loaded.', flush=True)

# 全局推理锁：HTTP 请求线程与后台连续检测线程串行使用同一模型
_infer_lock = threading.Lock()


def _predict_local(image):
    """本地推理，返回 (count, elapsed_ms, jpeg_bytes)。"""
    with _infer_lock:
        t0 = time.time()
        results = MODEL.predict(image, device=DEVICE, conf=CONF_THR, classes=[0], verbose=False)
        elapsed_ms = int((time.time() - t0) * 1000)
    r = results[0]
    count = int((r.boxes.cls == 0).sum().item())
    plotted = r.plot()
    ok, buf = cv2.imencode('.jpg', plotted, [cv2.IMWRITE_JPEG_QUALITY, 85])
    return count, elapsed_ms, (buf.tobytes() if ok else None)


def _predict_remote(image):
    """把帧转给 1238 /api/detectPreview，返回 (count, elapsed_ms, jpeg_bytes)。"""
    ok, buf = cv2.imencode('.jpg', image, [cv2.IMWRITE_JPEG_QUALITY, 90])
    if not ok:
        raise RuntimeError('帧编码失败')
    boundary = '----wbcompare%d' % int(time.time() * 1000)
    body = (('--%s\r\nContent-Disposition: form-data; name="file"; filename="frame.jpg"\r\n'
             'Content-Type: image/jpeg\r\n\r\n' % boundary).encode()
            + buf.tobytes() + ('\r\n--%s--\r\n' % boundary).encode())
    req = urllib.request.Request(
        DETECT_VIA + '/api/detectPreview', data=body,
        headers={'Content-Type': 'multipart/form-data; boundary=%s' % boundary})
    t0 = time.time()
    with urllib.request.urlopen(req, timeout=30) as resp:
        d = json.loads(resp.read().decode('utf-8'))
    if d.get('code') != '200':
        raise RuntimeError(d.get('msg') or '远程推理失败')
    jpg = base64.b64decode(d['image']) if d.get('image') else None
    return int(d['count']), int(d['elapsed_ms']), jpg


def _predict(image):
    """统一入口：本地或远程推理，返回 (count, elapsed_ms, jpeg_bytes)。"""
    if MODEL is None:
        return _predict_remote(image)
    return _predict_local(image)


def _alert(device_hint, count, fname, jpg):
    """检出人时推送 webhook（失败只记日志，不影响主流程）。"""
    if not ALERT_WEBHOOK:
        return
    try:
        payload = {'count': count, 'ts': int(time.time()), 'file': fname,
                   'source': device_hint or 'rtmp'}
        if ALERT_IMAGE and jpg:
            payload['image'] = base64.b64encode(jpg).decode('ascii')
        req = urllib.request.Request(
            ALERT_WEBHOOK, data=json.dumps(payload).encode('utf-8'),
            headers={'Content-Type': 'application/json'})
        urllib.request.urlopen(req, timeout=5)
        print('[compare] webhook 已推送 count=%d file=%s' % (count, fname), flush=True)
    except Exception as e:
        print('[compare] webhook 推送失败: %s' % e, flush=True)


@app.route('/')
def index():
    return send_from_directory(BASEDIR, 'index.html')


@app.route('/api/direct', methods=['POST'])
def direct():
    """YOLO 直接检测：同步返回标注图 / 人数 / 耗时，无任何副作用"""
    img = request.files.get('file')
    if img is None:
        return jsonify({'error': 'no file'}), 400
    data = np.frombuffer(img.read(), dtype=np.uint8)
    image = cv2.imdecode(data, cv2.IMREAD_COLOR)
    if image is None:
        return jsonify({'error': 'bad image'}), 400

    try:
        count, elapsed_ms, jpg = _predict(image)
    except Exception as e:
        return jsonify({'error': f'推理失败：{e}'}), 500
    return jsonify({
        'service': 'yolo11n',
        'device': DEVICE,
        'count': count,
        'elapsed_ms': elapsed_ms,
        'image': base64.b64encode(jpg).decode() if jpg else None,
    })


# ---------------------------------------------------------------- 常驻拉流
class StreamGrabber(object):
    """常驻 ffmpeg 拉流线程：持续解码 MJPEG 管道，只保留最新一帧。

    优点：不用每帧新建 RTMP 连接（握手 1~2s），帧率可到 5~15fps；
    断线自动重连，指数退避 1s -> 30s。
    """

    SOI = b'\xff\xd8'
    EOI = b'\xff\xd9'

    def __init__(self, url):
        self.url = url
        self.lock = threading.Lock()
        self.frame = None          # 最新完整 JPEG bytes
        self.ts = 0.0              # 最近一帧到达时间
        self.error = None          # 最近一次错误（None=正常）
        self._stop = threading.Event()
        self._proc = None
        self._thread = threading.Thread(target=self._loop, daemon=True,
                                        name='stream-grabber')
        self._thread.start()

    def _loop(self):
        backoff = 1
        while not self._stop.is_set():
            cmd = ['ffmpeg', '-hide_banner', '-v', 'error',
                   '-i', self.url,
                   '-f', 'mjpeg', '-q:v', '5', 'pipe:1']
            try:
                p = subprocess.Popen(cmd, stdout=subprocess.PIPE,
                                     stderr=subprocess.DEVNULL)
                self._proc = p
                backoff = 1
                buf = b''
                while not self._stop.is_set():
                    chunk = p.stdout.read(65536)
                    if not chunk:
                        break
                    buf += chunk
                    # 从管道字节流中切出完整 JPEG（SOI..EOI）
                    while True:
                        soi = buf.find(self.SOI)
                        if soi < 0:
                            buf = b''
                            break
                        eoi = buf.find(self.EOI, soi + 2)
                        if eoi < 0:
                            buf = buf[soi:]
                            break
                        frame = buf[soi:eoi + 2]
                        buf = buf[eoi + 2:]
                        with self.lock:
                            self.frame = frame
                            self.ts = time.time()
                            self.error = None
                p.terminate()
                try:
                    p.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    p.kill()
            except Exception as e:
                with self.lock:
                    self.error = str(e)[-200:]
            if self._stop.is_set():
                break
            with self.lock:
                self.error = '拉流中断，%ds 后重连' % backoff
            self._stop.wait(backoff)
            backoff = min(backoff * 2, 30)

    def latest(self, max_age=3.0):
        """返回 max_age 秒内的最新帧；过期/无帧返回 None。"""
        with self.lock:
            if self.frame and time.time() - self.ts <= max_age:
                return self.frame
            return None

    def close(self):
        self._stop.set()
        try:
            if self._proc:
                self._proc.terminate()
        except Exception:
            pass


# 全局只保留一个 Grabber；切换流地址时重建
_grabber = None
_grabber_lock = threading.Lock()


def _get_grabber(url):
    global _grabber
    with _grabber_lock:
        if _grabber is None or _grabber.url != url:
            if _grabber is not None:
                _grabber.close()
            _grabber = StreamGrabber(url)
        return _grabber


def _grab_frame(url, timeout=8):
    """取一帧：优先常驻 Grabber 的最新帧；没有则一次性 ffmpeg 抓帧兜底。"""
    frame = _get_grabber(url).latest(max_age=3.0)
    if frame:
        return frame
    cmd = ['timeout', str(timeout), 'ffmpeg', '-hide_banner', '-v', 'error',
           '-i', url, '-frames:v', '1', '-f', 'mjpeg', 'pipe:1']
    p = subprocess.run(cmd, capture_output=True, timeout=timeout + 5)
    if p.returncode != 0 or not p.stdout:
        msg = (p.stderr or b'').decode(errors='ignore')[-300:] or '抓帧失败'
        raise RuntimeError(msg)
    return p.stdout


@app.route('/api/rtmp/snapshot')
def rtmp_snapshot():
    """从 RTMP 流抓当前帧，直接返回 JPEG（用于页面轮询实现伪直播）"""
    url = utils.is_safe_rtmp_url(request.args.get('url') or RTMP_URL)
    if url is None:
        return jsonify({'error': '仅支持 rtmp:// 地址'}), 400
    try:
        jpg = _grab_frame(url)
    except Exception as e:
        return jsonify({'error': f'拉流失败：{e}'}), 502
    return Response(jpg, mimetype='image/jpeg', headers={'Cache-Control': 'no-store'})


@app.route('/api/rtmp/status')
def rtmp_status():
    """ffprobe 探测 RTMP 流：是否在线、编码、分辨率、帧率、音频"""
    url = utils.is_safe_rtmp_url(request.args.get('url') or RTMP_URL)
    if url is None:
        return jsonify({'error': '仅支持 rtmp:// 地址'}), 400
    cmd = ['timeout', '30', 'ffprobe', '-v', 'error',
           '-show_streams', '-show_format', '-of', 'json', url]
    try:
        p = subprocess.run(cmd, capture_output=True, timeout=35)
    except subprocess.TimeoutExpired:
        return jsonify({'online': False, 'error': '探测超时'}), 502
    if p.returncode != 0:
        return jsonify({'online': False,
                        'error': (p.stderr or b'').decode(errors='ignore')[-300:] or '连接失败'})
    info = json.loads(p.stdout or '{}')
    video = audio = None
    for s in info.get('streams', []):
        if s.get('codec_type') == 'video' and video is None:
            video = {'codec': s.get('codec_name'), 'width': s.get('width'),
                     'height': s.get('height'), 'profile': s.get('profile')}
        elif s.get('codec_type') == 'audio' and audio is None:
            audio = {'codec': s.get('codec_name'), 'sample_rate': s.get('sample_rate'),
                     'channels': s.get('channels')}
    return jsonify({'online': True, 'video': video, 'audio': audio,
                    'format': (info.get('format') or {}).get('format_name')})


@app.route('/api/rtmp/detect', methods=['POST'])
def rtmp_detect():
    """抓 RTMP 当前帧并用 YOLO 检测，返回标注图 + 人数 + 耗时"""
    url = utils.is_safe_rtmp_url(request.form.get('url') or RTMP_URL)
    if url is None:
        return jsonify({'error': '仅支持 rtmp:// 地址'}), 400
    try:
        jpg = _grab_frame(url)
    except Exception as e:
        return jsonify({'error': f'拉流失败：{e}'}), 502
    image = cv2.imdecode(np.frombuffer(jpg, dtype=np.uint8), cv2.IMREAD_COLOR)
    if image is None:
        return jsonify({'error': '抓到的帧无法解码'}), 502

    try:
        count, elapsed_ms, plotted = _predict(image)
    except Exception as e:
        return jsonify({'error': f'推理失败：{e}'}), 500
    return jsonify({
        'service': 'yolo11n @ RTMP',
        'device': DEVICE,
        'count': count,
        'elapsed_ms': elapsed_ms,
        'image': base64.b64encode(plotted).decode() if plotted else None,
    })


# ---- RTMP 连续检测：后台线程 取帧 → YOLO → 落盘 ----
DETECT_DIR = os.environ.get('WC_DETECT_DIR', os.path.join(BASEDIR, 'detections'))
os.makedirs(DETECT_DIR, exist_ok=True)
MAX_SAVED = int(os.environ.get('WC_MAX_SAVED', '50'))   # 图库最多保留张数（5 页 × 10 张）
GALLERY_PAGE_SIZE = int(os.environ.get('WC_PAGE_SIZE', '10'))   # 图库每页张数
AD_INTERVAL = float(os.environ.get('WC_INTERVAL', '0.8'))       # 每轮检测间隔（秒）
SAVE_HASH_DIST = int(os.environ.get('WC_SAVE_HASH_DIST', '10'))  # 图库去重：与上一张 dHash 距离 <= 该值不保存

_ad_state = {'running': False, 'count': 0, 'elapsed_ms': 0,
             'frames': 0, 'saved': 0, 'error': None, 'last_file': None, 'fps': 0.0}
_ad_lock = threading.Lock()
_ad_thread = None
_ad_stop = threading.Event()
_ad_stamps = deque(maxlen=20)   # 最近帧时间戳，用于计算实际帧率


def _write_jpg(path, jpg_bytes):
    """JPEG 字节直接落盘（不做多余解码/再编码）。"""
    try:
        with open(path, 'wb') as f:
            f.write(jpg_bytes)
    except OSError as e:
        print('[compare] 写图失败 %s: %s' % (path, e), flush=True)


def _cleanup_detections():
    """图库超出 MAX_SAVED 时删除最旧的（latest.jpg 不计入），返回删除数量"""
    files = [f for f in os.listdir(DETECT_DIR) if f.startswith('detect_') and f.endswith('.jpg')]
    if len(files) <= MAX_SAVED:
        return 0
    files.sort(key=lambda f: os.path.getmtime(os.path.join(DETECT_DIR, f)))
    removed = 0
    for f in files[:len(files) - MAX_SAVED]:
        try:
            os.remove(os.path.join(DETECT_DIR, f))
            removed += 1
        except OSError:
            pass
    return removed


def _autodetect_loop(url):
    """连续检测循环：取帧 → YOLO 检测 → 写 latest.jpg；检出人且画面变化时存入图库"""
    grabber = _get_grabber(url)
    last_hash = None          # 上一张已保存图的 dHash（图库去重）
    fail_count = 0
    while not _ad_stop.is_set():
        try:
            jpg = grabber.latest(max_age=2.0)
            if jpg is None:
                # 常驻流无新鲜帧：等一拍，让 Grabber 自己重连（指数退避由它管）
                with _ad_lock:
                    _ad_state['error'] = grabber.error or '等待视频帧…'
                _ad_stop.wait(1.0)
                continue
            fail_count = 0
            image = cv2.imdecode(np.frombuffer(jpg, dtype=np.uint8), cv2.IMREAD_COLOR)
            if image is None:
                raise RuntimeError('抓到的帧无法解码')

            count, elapsed_ms, plotted = _predict(image)

            fname = None
            if plotted:
                _write_jpg(os.path.join(DETECT_DIR, 'latest.jpg'), plotted)
            if count > 0:
                h = utils.dhash(image)
                # 图库去重：与上一张保存的画面几乎一致则跳过，避免静置画面刷屏
                if last_hash is None or utils.hamming(h, last_hash) > SAVE_HASH_DIST:
                    fname = "detect_%s_%dp.jpg" % (time.strftime('%Y%m%d_%H%M%S'), count)
                    _write_jpg(os.path.join(DETECT_DIR, fname), plotted)
                    _cleanup_detections()
                    last_hash = h
                    threading.Thread(target=_alert, args=(url, count, fname, plotted),
                                     daemon=True).start()

            with _ad_lock:
                _ad_state['count'] = count
                _ad_state['elapsed_ms'] = elapsed_ms
                _ad_state['frames'] += 1
                _ad_state['error'] = None
                _ad_state['last_file'] = fname
                if fname:
                    _ad_state['saved'] += 1
            _ad_stamps.append(time.time())
            with _ad_lock:   # 用最近若干帧计算实际帧率
                if len(_ad_stamps) >= 2:
                    span = _ad_stamps[-1] - _ad_stamps[0]
                    _ad_state['fps'] = round((len(_ad_stamps) - 1) / span, 1) if span > 0 else 0.0
        except Exception as e:
            fail_count += 1
            with _ad_lock:
                _ad_state['error'] = str(e)[-200:]
        _ad_stop.wait(AD_INTERVAL)


def _start_autodetect(url):
    global _ad_thread
    with _ad_lock:
        if _ad_state['running']:
            return False
        _ad_state.update(running=True, frames=0, saved=0, error=None, fps=0.0,
                         count=0, elapsed_ms=0, last_file=None)
    _ad_stop.clear()
    _ad_stamps.clear()
    _ad_thread = threading.Thread(target=_autodetect_loop, args=(url,), daemon=True)
    _ad_thread.start()
    return True


@app.route('/api/rtmp/autodetect/start', methods=['POST'])
def rtmp_autodetect_start():
    """启动后台连续检测：取帧检测，检出人且画面变化时保存标注图"""
    url = utils.is_safe_rtmp_url(request.form.get('url') or RTMP_URL)
    if url is None:
        return jsonify({'error': '仅支持 rtmp:// 地址'}), 400
    started = _start_autodetect(url)
    return jsonify({'started': started,
                    'msg': '已在运行' if not started else '连续检测已启动'})


@app.route('/api/rtmp/autodetect/stop', methods=['POST'])
def rtmp_autodetect_stop():
    with _ad_lock:
        _ad_state['running'] = False
    _ad_stop.set()
    return jsonify({'stopped': True})


@app.route('/api/rtmp/autodetect/state')
def rtmp_autodetect_state():
    """连续检测实时状态（页面每秒轮询）"""
    with _ad_lock:
        return jsonify(_ad_state)


@app.route('/api/rtmp/gallery')
def rtmp_gallery():
    """检测图库列表（最新在前），分页返回
    参数：page（1 起，默认 1）、size（默认 GALLERY_PAGE_SIZE）
    返回：items（当前页）、total、pages、page、size
    """
    try:
        page = max(1, int(request.args.get('page', 1)))
    except (TypeError, ValueError):
        page = 1
    try:
        size = int(request.args.get('size', GALLERY_PAGE_SIZE))
    except (TypeError, ValueError):
        size = GALLERY_PAGE_SIZE
    size = max(1, min(size, 50))

    items = []
    for f in os.listdir(DETECT_DIR):
        if not (f.startswith('detect_') and f.endswith('.jpg')):
            continue
        p = os.path.join(DETECT_DIR, f)
        try:
            st = os.stat(p)
            items.append({'name': f, 'mtime': int(st.st_mtime),
                          'size': st.st_size, 'people': utils.people_from_name(f)})
        except OSError:
            continue
    items.sort(key=lambda x: -x['mtime'])
    total = len(items)
    pages = max(1, (total + size - 1) // size)
    page = min(page, pages)
    start = (page - 1) * size
    return jsonify({'items': items[start:start + size], 'total': total,
                    'pages': pages, 'page': page, 'size': size})


@app.route('/detections/<path:fname>')
def detections_file(fname):
    """访问检测图片（latest.jpg 或图库文件）"""
    return send_from_directory(DETECT_DIR, fname)


@app.route('/api/health')
def health():
    """健康检查：服务、模型、连续检测状态"""
    with _ad_lock:
        ad = {'running': _ad_state['running'], 'fps': _ad_state['fps'],
              'frames': _ad_state['frames'], 'saved': _ad_state['saved'],
              'error': _ad_state['error']}
    try:
        saved = len([f for f in os.listdir(DETECT_DIR)
                     if f.startswith('detect_') and f.endswith('.jpg')])
    except OSError:
        saved = -1
    return jsonify({'code': '200', 'msg': 'ok', 'version': VERSION, 'device': DEVICE,
                    'port': PORT, 'autodetect': ad, 'gallery': saved,
                    'rtmp': RTMP_URL, 'detect_dir': DETECT_DIR,
                    'detect_via': DETECT_VIA or None})


if __name__ == '__main__':
    _removed = _cleanup_detections()   # 启动时按上限清理一次历史图库
    if _removed:
        print('[compare] 图库超出 %d 张，已清理最旧 %d 张' % (MAX_SAVED, _removed), flush=True)
    app.run(host="0.0.0.0", port=PORT, threaded=True)
