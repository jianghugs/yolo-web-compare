# yolo_web_compare 变更记录

## 1.3.0 — 2026-11-26

### 性能
- **常驻 ffmpeg 拉流（StreamGrabber）**：不再每帧新建 RTMP 连接（原握手 1~2s/帧），
  持续解码只取最新帧，连续检测帧率从 ~1fps 提升到 5~15fps；断线指数退避重连（1s→30s）。

### 正确性 / 体验
- **全局推理锁**：HTTP 检测线程与后台连续检测线程串行使用模型，消除并发压模型的隐患。
- **图库保存去重**：检出画面与上一张 dHash 距离 <= `WC_SAVE_HASH_DIST`（默认 10）时不再落盘，
  静置画面不再刷屏图库。
- **XSS 加固**：图库文件名 `encodeURIComponent` 后再进 HTML/URL。
- `BASEDIR` 改用 `os.path` 实现，兼容 Windows。

### 新增
- `WC_DETECT_VIA`：可把推理转发给 1238 的 `/api/detectPreview`，本进程不加载模型（省一半显存）。
- `WC_ALERT_WEBHOOK` / `WC_ALERT_IMAGE`：检出人时向 webhook 推送 JSON 通知（可选带标注图）。
- 工程化：`Dockerfile`（含 ffmpeg）、GitHub Actions CI、pytest 单元测试（utils 6 项）、
  `LICENSE`（MIT）、`tools/export_trt.sh`、依赖版本锁定。

## 1.3.0 — 2026-11-26

### 性能
- **常驻 ffmpeg 拉流（StreamGrabber）**：不再每帧新建 RTMP 连接（原握手 1~2s/帧），
  持续解码只取最新帧，连续检测帧率从 ~1fps 提升到 5~15fps；断线指数退避重连（1s→30s）。

### 正确性 / 体验
- **全局推理锁**：HTTP 检测线程与后台连续检测线程串行使用模型，消除并发压模型的隐患。
- **图库保存去重**：检出画面与上一张 dHash 距离 <= `WC_SAVE_HASH_DIST`（默认 10）时不再落盘，
  静置画面不再刷屏图库。
- **XSS 加固**：图库文件名 `encodeURIComponent` 后再进 HTML/URL。
- `BASEDIR` 改用 `os.path` 实现，兼容 Windows。

### 新增
- `WC_DETECT_VIA`：可把推理转发给 1238 的 `/api/detectPreview`，本进程不加载模型（省一半显存）。
- `WC_ALERT_WEBHOOK` / `WC_ALERT_IMAGE`：检出人时向 webhook 推送 JSON 通知（可选带标注图）。
- 工程化：`Dockerfile`（含 ffmpeg）、GitHub Actions CI、pytest 单元测试（utils 6 项）、
  `LICENSE`（MIT）、`tools/export_trt.sh`、依赖版本锁定。

## 1.2.1 — 2026-11-20

### 图库改为分页 + 限制保留量

- `GET /api/rtmp/gallery` 支持分页：`page`（1 起）、`size`（默认 10，上限 50），返回 `items / total / pages / page / size`；越界自动夹取。
- 页面图库每页 **10 张**，底部新增「上一页 / 下一页 + 页码」分页条，显示「第 x / y 页 · 本页 N 张」；缩略图角标加上序号 `#n`。
- 图库保留上限从 300 改为 **50 张（5 页）**：`WC_MAX_SAVED` 默认 50，另新增 `WC_PAGE_SIZE`（默认 10）。
- 服务启动时按上限清理一次历史图库，避免旧数据长期占盘（清理数量打印到 service.log）。

## 1.2.0 — 2026-11-20

### 变更：去掉旧服务对比，回归单一检测工具

- **移除** `POST /api/direct_old`（Pedestron 子进程推理）、`POST /api/flow`（1237/1238 各传 3 次）及相关代码（`tempfile`、`requests`、`OLD_*`/`NEW_URL` 配置）。
- **移除** 页面上的「旧服务直接预览」「完整接口流程对比」按钮与旧服务结果卡片，页面改为单列布局：实时视频流 → 检测图库 → 单图检测。
- **健康检查** `/api/health` 精简：去掉上游 1237/1238 探测，改为返回版本、设备、端口、连续检测状态（含 `error`）、图库张数；不再依赖 `requests`。
- **systemd 单元**：`Description` 改为 `Pedestrian Detection Web Console (YOLO11, port 1239)`，去掉对 `yolo-service.service` 的启动依赖（两个服务互不影响）。
- **页面**：标题改为「行人检测 · YOLO11 实时监控」，副标题显示服务健康状态；图库增加「刷新」按钮，时间显示改为月-日 时:分:秒；连续检测在他处停止时按钮自动复位。
- 文档同步更新（README / DEPLOY 去掉对比说明与旧服务排错条目）。

## 1.1.0 — 2026-10-29

### 新增：RTMP 实时视频检测

- **流状态探测** `GET /api/rtmp/status`：ffprobe 返回在线状态、视频编码/分辨率/profile、音频编码/采样率。
- **实时预览** `GET /api/rtmp/snapshot`：服务端 ffmpeg 抓帧返回 JPEG，页面轮询显示（伪直播）；带 0.8 秒结果缓存 + 线程锁，多客户端同时观看只抓一次流。
- **连续检测** `POST /api/rtmp/autodetect/start|stop` + `GET /api/rtmp/autodetect/state`：后台线程按 `WC_INTERVAL`（默认 0.8s）循环抓帧 → YOLO 推理 → 刷新 `detections/latest.jpg`；检出人时另存 `detect_时间_人数p.jpg`。
- **状态展示**：页面实时显示当前人数、本轮推理耗时、**帧率 fps**（最近 20 帧滑动窗口）、累计帧数、已存档数。
- **检测图库** `GET /api/rtmp/gallery` + `GET /detections/<file>`：页面缩略图网格浏览，每 10 秒自动刷新，点击看原图；最多保留 `WC_MAX_SAVED`（默认 300）张，超出自动删最旧。

### 新增：配置化与可运维性

- 全部参数支持环境变量：`WC_PORT`、`WC_RTMP_URL`、`WC_CONF`、`WC_DETECT_DIR`、`WC_MAX_SAVED`、`WC_INTERVAL`、`WC_OLD_URL`、`WC_NEW_URL`。
- 新增 `GET /api/health`：返回版本、设备、端口、连续检测状态、上游 1237/1238 连通性、图库目录。
- 新增 `deploy.sh`（备份 → 覆盖 → 重启 → 健康检查 → 失败自动回滚，支持 `--rollback`）、`tools/package.sh`（打包 + SHA256）。
- 新增完整文档：`README.md`、`DEPLOY.md`、`CHANGELOG.md`、`VERSION`。

### 修复

- ffmpeg 4.4.2 不支持 `-stimeout` 导致抓帧失败 → 改为外层 `timeout` 控制超时。
- 页面“YOLO 检测当前画面”由单次检测改为连续检测开关，避免一直点、结果不留存。

## 1.0.0 — 2026-10-21

### 首个版本

- 单页对比工具：`index.html` + Flask 后端（1239）。
- `POST /api/direct`：本进程 YOLO11n 直接推理，返回标注图/人数/耗时，无副作用。
- `POST /api/direct_old`：子进程调用 `Pedestron/demo/predict_once.py`（open-mmlab 环境），避免 torch 版本冲突；显存不足自动退 CPU。
- `POST /api/flow`：同一张图分别向 1237 与 1238 各上传 3 次，返回逐步 HTTP 状态、响应体、耗时（旧服务会真实回调告警平台，页面有醒目提示）。
- systemd 托管 `yolo-compare.service`，日志 append 写入 + logrotate 轮转。
