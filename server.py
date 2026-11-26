# -*- coding: utf-8 -*-
"""
行人检测 Web 控制台（端口 1239）—— 只做 YOLO 检测，不再对比旧服务
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

启动：/home/deployer/miniconda3/envs/yolo11/bin/python server.py   （见 start.sh）
"""
import base64
import json
import os
import subprocess
import threading
import time
from collections import deque

import cv2
import numpy as np
from flask import Flask, request, jsonify, send_from_directory, Response

from ultralytics import YOLO
import torch

CONF_THR = float(os.environ.get('WC_CONF', '0.5'))
PORT = int(os.environ.get('WC_PORT', '1239'))
VERSION = '1.2.1'

# RTMP 实时流（可在页面上改，这里只是默认值）
RTMP_URL = os.environ.get('WC_RTMP_URL', 'rtmp://<RTMP_HOST>:1935/live/<STREAM_KEY>')

BASEDIR = __file__.rsplit('/', 1)[0]

app = Flask(__name__, static_folder=None)

# ---- YOLO 模型：启动加载一次 ----
DEVICE = 'cuda:0' if torch.cuda.is_available() else 'cpu'
print(f'[compare] loading YOLO on {DEVICE} ...', flush=True)
MODEL = YOLO(BASEDIR + '/yolo11n.pt')
if DEVICE == 'cuda:0':
    MODEL.to(DEVICE)
print('[compare] model loaded.', flush=True)


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

    t0 = time.time()
    results = MODEL.predict(image, device=DEVICE, conf=CONF_THR, classes=[0], verbose=False)
    elapsed_ms = int((time.time() - t0) * 1000)
    r = results[0]
    count = int((r.boxes.cls == 0).sum().item())
    plotted = r.plot()
    ok, buf = cv2.imencode('.jpg', plotted, [cv2.IMWRITE_JPEG_QUALITY, 85])
    return jsonify({
        'service': 'yolo11n',
        'device': DEVICE,
        'count': count,
        'elapsed_ms': elapsed_ms,
        'image': base64.b64encode(buf.tobytes()).decode() if ok else None,
    })


def _safe_rtmp_url(url):
    """只允许 rtmp:// 协议，避免 SSRF"""
    if not url or not url.strip().lower().startswith('rtmp://'):
        return None
    return url.strip()


def _grab_frame(url, timeout=15):
    """ffmpeg 从 RTMP 流抓一帧 JPEG，返回 bytes；失败抛异常"""
    cmd = ['timeout', str(timeout), 'ffmpeg', '-hide_banner', '-v', 'error',
           '-i', url,
           '-frames:v', '1', '-f', 'mjpeg', 'pipe:1']
    p = subprocess.run(cmd, capture_output=True, timeout=timeout + 5)
    if p.returncode != 0 or not p.stdout:
        msg = (p.stderr or b'').decode(errors='ignore')[-300:] or '抓帧失败'
        raise RuntimeError(msg)
    return p.stdout


# 抓帧结果短缓存（0.8 秒）：多个浏览器同时轮询时只抓一次流
_snap_lock = threading.Lock()
_snap_cache = {'ts': 0.0, 'jpg': None, 'url': None}


@app.route('/api/rtmp/snapshot')
def rtmp_snapshot():
    """从 RTMP 流抓当前帧，直接返回 JPEG（用于页面轮询实现伪直播）"""
    url = _safe_rtmp_url(request.args.get('url') or RTMP_URL)
    if url is None:
        return jsonify({'error': '仅支持 rtmp:// 地址'}), 400
    with _snap_lock:
        if _snap_cache['jpg'] and time.time() - _snap_cache['ts'] < 0.8 and _snap_cache['url'] == url:
            jpg = _snap_cache['jpg']
        else:
            try:
                jpg = _grab_frame(url)
            except Exception as e:
                return jsonify({'error': f'拉流失败：{e}'}), 502
            _snap_cache.update(ts=time.time(), jpg=jpg, url=url)
    return Response(jpg, mimetype='image/jpeg', headers={'Cache-Control': 'no-store'})


@app.route('/api/rtmp/status')
def rtmp_status():
    """ffprobe 探测 RTMP 流：是否在线、编码、分辨率、帧率、音频"""
    url = _safe_rtmp_url(request.args.get('url') or RTMP_URL)
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
    url = _safe_rtmp_url(request.form.get('url') or RTMP_URL)
    if url is None:
        return jsonify({'error': '仅支持 rtmp:// 地址'}), 400
    try:
        jpg = _grab_frame(url)
    except Exception as e:
        return jsonify({'error': f'拉流失败：{e}'}), 502
    image = cv2.imdecode(np.frombuffer(jpg, dtype=np.uint8), cv2.IMREAD_COLOR)
    if image is None:
        return jsonify({'error': '抓到的帧无法解码'}), 502

    t0 = time.time()
    results = MODEL.predict(image, device=DEVICE, conf=CONF_THR, classes=[0], verbose=False)
    elapsed_ms = int((time.time() - t0) * 1000)
    r = results[0]
    count = int((r.boxes.cls == 0).sum().item())
    plotted = r.plot()
    ok, buf = cv2.imencode('.jpg', plotted, [cv2.IMWRITE_JPEG_QUALITY, 85])
    return jsonify({
        'service': 'yolo11n @ RTMP',
        'device': DEVICE,
        'count': count,
        'elapsed_ms': elapsed_ms,
        'image': base64.b64encode(buf.tobytes()).decode() if ok else None,
    })


# ---- RTMP 连续检测：后台线程 抓帧 → YOLO → 落盘 ----
DETECT_DIR = os.environ.get('WC_DETECT_DIR', os.path.join(BASEDIR, 'detections'))
os.makedirs(DETECT_DIR, exist_ok=True)
MAX_SAVED = int(os.environ.get('WC_MAX_SAVED', '50'))   # 图库最多保留张数（5 页 × 10 张），超出自动删最旧
GALLERY_PAGE_SIZE = int(os.environ.get('WC_PAGE_SIZE', '10'))   # 图库每页张数
AD_INTERVAL = float(os.environ.get('WC_INTERVAL', '0.8'))  # 每轮检测间隔（秒）

_ad_state = {'running': False, 'count': 0, 'elapsed_ms': 0,
             'frames': 0, 'saved': 0, 'error': None, 'last_file': None, 'fps': 0.0}
_ad_lock = threading.Lock()
_ad_thread = None
_ad_stop = threading.Event()
_ad_stamps = deque(maxlen=20)   # 最近帧时间戳，用于计算实际帧率


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
    """连续检测循环：抓帧 → YOLO 检测 → 保存 latest.jpg；检出人时另存入图库"""
    while not _ad_stop.is_set():
        try:
            jpg = _grab_frame(url, timeout=15)
            image = cv2.imdecode(np.frombuffer(jpg, dtype=np.uint8), cv2.IMREAD_COLOR)
            if image is None:
                raise RuntimeError('抓到的帧无法解码')

            t0 = time.time()
            results = MODEL.predict(image, device=DEVICE, conf=CONF_THR, classes=[0], verbose=False)
            elapsed_ms = int((time.time() - t0) * 1000)
            r = results[0]
            count = int((r.boxes.cls == 0).sum().item())

            fname = None
            cv2.imwrite(os.path.join(DETECT_DIR, 'latest.jpg'), r.plot(),
                        [cv2.IMWRITE_JPEG_QUALITY, 85])
            if count > 0:   # 检出人：另存带时间戳和人数的标注图
                fname = "detect_%s_%dp.jpg" % (time.strftime('%Y%m%d_%H%M%S'), count)
                cv2.imwrite(os.path.join(DETECT_DIR, fname), r.plot(),
                            [cv2.IMWRITE_JPEG_QUALITY, 85])
                _cleanup_detections()

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
    """启动后台连续检测：每约0.8秒抓一帧检测，检出人时保存标注图"""
    url = _safe_rtmp_url(request.form.get('url') or RTMP_URL)
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
                          'size': st.st_size, 'people': _people_from_name(f)})
        except OSError:
            continue
    items.sort(key=lambda x: -x['mtime'])
    total = len(items)
    pages = max(1, (total + size - 1) // size)
    page = min(page, pages)
    start = (page - 1) * size
    return jsonify({'items': items[start:start + size], 'total': total,
                    'pages': pages, 'page': page, 'size': size})


def _people_from_name(fname):
    """从文件名 detect_20261029_095812_2p.jpg 解析检出人数"""
    try:
        return int(fname.rsplit('_', 1)[1].split('p')[0])
    except (ValueError, IndexError):
        return -1


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
                    'rtmp': RTMP_URL, 'detect_dir': DETECT_DIR})


if __name__ == '__main__':
    _removed = _cleanup_detections()   # 启动时按上限清理一次历史图库
    if _removed:
        print('[compare] 图库超出 %d 张，已清理最旧 %d 张' % (MAX_SAVED, _removed), flush=True)
    app.run(host="0.0.0.0", port=PORT, threaded=True)
