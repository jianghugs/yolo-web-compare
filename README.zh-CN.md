# yolo_web_compare — 行人检测 Web 控制台

> 🌐 语言 / Language: [English](README.md) | 简体中文]

基于 Ultralytics **YOLO11n** 的行人检测 Web 控制台（端口 **1239**），一个页面完成：

1. **RTMP 实时预览** —— 从摄像头推流地址抓帧轮询，浏览器里看“伪直播”（约 1~1.5 帧/秒）；
2. **流上连续检测** —— 后台线程每约 0.8 秒抓一帧 → YOLO 检测 → 刷新检测画面，**检出人自动存档**；
3. **检测图库** —— 检出画面按时间倒序分页展示（每页 10 张，最多保留 5 页 / 50 张），带人数与时间角标，可点开放大；
4. **单图检测** —— 上传一张图片即时检测，返回标注图 + 人数 + 耗时，不留档、不回调。

> v1.2.0 起**已移除**与旧 Pedestron 服务（1237）的对比功能，页面只服务 YOLO 检测本身。

---

## 1. 架构

```
浏览器 ──HTTP──> server.py (Flask, :1239)
                  ├── /api/rtmp/snapshot    → ffmpeg 抓一帧 → JPEG
                  ├── /api/rtmp/status      → ffprobe 探测流信息
                  ├── /api/rtmp/autodetect  → 后台线程：抓帧 → YOLO → 落盘
                  ├── /api/direct           → 单图 YOLO 检测
                  └── /api/rtmp/gallery     → detections/ 目录清单
                                   │
                                   ├── detections/latest.jpg（最新检测画面）
                                   └── detections/detect_<时间>_<人数>p.jpg（图库）
```

- 模型：`yolo11n.pt`（COCO 预训练，`classes=[0]` 只检人），GPU 可用时 `cuda:0`，否则 CPU。
- 抓帧：调用系统 `ffmpeg`（Ubuntu 22.04 自带 4.4.2），每次抓帧都是独立连接，超时由外层 `timeout` 控制。
- 并发：抓帧有 0.8 秒结果缓存 + 线程锁，多个浏览器同时看只抓一次流。

## 2. 目录结构

```
yolo_web_compare/
├── server.py                 # 全部后端
├── index.html                # 页面（单文件，无外部依赖）
├── yolo11n.pt                # 模型权重（5.6MB）
├── start.sh                  # 前台启动脚本
├── deploy.sh                 # 一键部署（备份 + 健康检查 + 自动回滚）
├── systemd/
│   ├── yolo-compare.service  # 本服务（1239）
│   └── yolo-compare.logrotate
├── tools/package.sh          # 打包 dist/yolo_web_compare-<ver>.tar.gz
├── detections/               # 运行期生成：检测画面
├── dist/                     # 打包产物
├── README.md / DEPLOY.md / CHANGELOG.md / VERSION
└── requirements.txt
```

## 3. 接口

| 方法 | 路径 | 参数 | 说明 |
|---|---|---|---|
| GET | `/` | — | 页面 |
| GET | `/api/health` | — | 版本、设备、端口、连续检测状态、图库张数 |
| POST | `/api/direct` | `file` | 单图检测，返回 `count / elapsed_ms / device / image(base64)` |
| GET | `/api/rtmp/status` | `url`(可选) | ffprobe 探测：`online / video / audio / format` |
| GET | `/api/rtmp/snapshot` | `url`(可选) | 抓当前帧返回 JPEG（`Cache-Control: no-store`） |
| POST | `/api/rtmp/detect` | `url` | 抓帧 + 单次 YOLO 检测 |
| POST | `/api/rtmp/autodetect/start` | `url` | 启动后台连续检测 |
| POST | `/api/rtmp/autodetect/stop` | — | 停止连续检测 |
| GET | `/api/rtmp/autodetect/state` | — | `running / count / elapsed_ms / fps / frames / saved / error` |
| GET | `/api/rtmp/gallery` | `page`, `size` | 图库分页列表（默认每页 10 张），返回 `items / total / pages / page / size` |
| GET | `/detections/<file>` | — | 查看检测图片 |

示例：

```bash
curl -s http://127.0.0.1:1239/api/health

curl -s -X POST -F "file=@test.jpg" http://127.0.0.1:1239/api/direct

curl -s "http://127.0.0.1:1239/api/rtmp/status?url=rtmp://<RTMP_HOST>:1935/live/xxx"

curl -s -X POST -d "url=rtmp://<RTMP_HOST>:1935/live/xxx" \
     http://127.0.0.1:1239/api/rtmp/autodetect/start
sleep 5 && curl -s http://127.0.0.1:1239/api/rtmp/autodetect/state
curl -s -X POST http://127.0.0.1:1239/api/rtmp/autodetect/stop
```

## 4. 环境变量

| 变量 | 默认值 | 说明 |
|---|---|---|
| `WC_PORT` | `1239` | 监听端口 |
| `WC_RTMP_URL` | `rtmp://<RTMP_HOST>:1935/live/<STREAM_KEY>` | 默认流地址（页面可改） |
| `WC_CONF` | `0.5` | 置信度阈值 |
| `WC_INTERVAL` | `0.8` | 连续检测每轮间隔（秒） |
| `WC_DETECT_DIR` | `<项目>/detections` | 检测图保存目录 |
| `WC_MAX_SAVED` | `50` | 图库最多保留张数（5 页 × 10 张），超出自动删最旧；启动时也会清理一次 |
| `WC_PAGE_SIZE` | `10` | 图库每页张数（前端每页 10 张） |

systemd 部署时写在 `compare.env`（一行一个 `KEY=VALUE`），unit 里用 `EnvironmentFile=-` 引入。

## 5. 快速开始

```bash
# 服务器上（conda 环境已含 torch + ultralytics + flask + opencv）
cd /home/deployer/yolo_web_compare
bash start.sh                      # 前台跑，Ctrl+C 停

# 或 systemd
sudo systemctl restart yolo-compare
sudo systemctl status yolo-compare --no-pager
```

浏览器打开 `http://<SERVER_IP>:1239/`。

## 6. 页面使用

1. **看实时画面**：确认流地址 → 「开始直播预览」，左侧出现 LIVE 画面（拉流失败会提示且自动停止）。
2. **开始检测**：点「开始连续检测」，右侧显示标注画面，状态栏实时显示 `检出人数 | 本轮耗时 | 帧率 | 已检帧数 | 已存张数`。
3. **看图库**：检出人的画面自动落到下方「检测图库」，**每页 10 张**，用「上一页 / 下一页」或页码翻页；每 10 秒自动刷新，也可手动「刷新」；点击缩略图看原图。
4. **单图检测**：拖入图片 → 「开始检测」，返回标注图与指标。

## 7. 打包与分发

```bash
bash tools/package.sh
# dist/yolo_web_compare-1.2.1.tar.gz (+ .sha256)，包内 MANIFEST.sha256 可逐文件校验
```

## 8. 已知限制

- **帧率上限约 1 fps**：每轮都是“新建 ffmpeg 连接 → 握手 → 抓一帧”，抓帧本身耗时 1~2 秒，是瓶颈（检测仅 8~10ms）。若要 5~10 fps，需改成常驻 ffmpeg 进程持续解码、取最新帧。
- 图库按文件名解析人数（`detect_<时间>_<N>p.jpg`），手工改名会导致人数显示 `-1`。
- 图库只保留最近 50 张（5 页），更早的会被自动删除，需要长期留档请另行同步到其他存储。
- 抓帧依赖系统 `ffmpeg`，无 ffmpeg 时接口返回 502。
- 流中断时连续检测线程会持续重试并在状态里报错；需摄像头端恢复推流后才会自动继续出画面。
