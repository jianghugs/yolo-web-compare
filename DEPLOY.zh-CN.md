# yolo_web_compare 部署运维手册

> 🌐 语言 / Language: [English](DEPLOY.md) | 简体中文]

> 目标机：`<SERVER_IP>`（Ubuntu 22.04），运行用户 `deployer`
> 本手册覆盖：环境依赖 → 首次部署 → systemd 托管 → 验收 → 升级/回滚 → 排错。

## 0. 三服务总览

| 端口 | systemd 单元 | 作用 | 运行环境 |
|---|---|---|---|
| 1237 | `pedestron-legacy` | 旧 Pedestron 行人检测（对接告警平台） | `/home/deployer/miniconda3/envs/open-mmlab` |
| 1238 | `yolo-service` | 新 YOLO11 行人检测（协议兼容旧版） | `/home/deployer/miniconda3/envs/yolo11` |
| 1239 | `yolo-compare` | **本工具**：行人检测控制台（RTMP 直播 + 连续检测 + 图库 + 单图检测） | `/home/deployer/miniconda3/envs/yolo11` |

依赖关系：1239 完全独立——不请求 1237/1238，只调用本机 `ffmpeg` 抓流 + 本地 YOLO 模型推理。

## 1. 环境要求

| 项 | 要求 | 检查命令 |
|---|---|---|
| OS | Ubuntu 22.04（或同版本 glibc 发行版） | `lsb_release -a` |
| Python | 3.10（conda 环境 `yolo11`） | `conda env list` |
| Python 包 | torch 2.5.1+cu121、ultralytics、flask、opencv | `python -c "import torch,ultralytics,cv2;print(torch.cuda.is_available())"` |
| 系统工具 | **ffmpeg / ffprobe ≥ 4.4**（抓帧必需） | `ffmpeg -version \| head -1` |
| GPU | 可选；无 GPU 自动落 CPU（`DEVICE='cpu'`） | `nvidia-smi` |
| 磁盘 | 图库按 300 张 × ~90KB ≈ 30MB，建议预留 ≥1GB | `df -h /home` |

安装系统依赖（若无 ffmpeg）：

```bash
sudo apt update && sudo apt install -y ffmpeg
```

Python 依赖（`requirements.txt` 已列出）：正常情况下 `yolo11` 环境里已全部具备，缺失时：

```bash
/home/deployer/miniconda3/envs/yolo11/bin/pip install -r requirements.txt \
    -i https://mirrors.aliyun.com/pypi/simple/
```

> ⚠️ 不要在这个环境里 `pip install -U torch`。环境里必须保持 `torch 2.5.1+cu121`，
> 被升到 `+cu13*` 会出现 `CUDNN_STATUS_NOT_INITIALIZED`（详见第 8 节）。

## 2. 首次部署

### 方式 A：包部署（推荐）

```bash
# 1) 上传包并校验
scp yolo_web_compare-1.2.1.tar.gz* deployer@<SERVER_IP>:~/
sha256sum -c yolo_web_compare-1.2.1.tar.gz.sha256

# 2) 解包
tar -xzf yolo_web_compare-1.2.1.tar.gz && cd yolo_web_compare-1.2.1

# 3) 一键部署（内部会：停服务 → 备份到 backup/ → 覆盖文件 → 装 unit → 启动 → 健康检查）
bash deploy.sh
```

`deploy.sh` 关键行为：

- 自动检测 `yolo11n.pt` 是否缺失（缺失则从旧目录或包内补齐）；
- 覆盖前把现有目录打包到 `backup/compare-<时间戳>.tar.gz`；
- 用 `sudo -S` 安装/刷新 `/etc/systemd/system/yolo-compare.service`；
- 启动后轮询 `http://127.0.0.1:1239/api/health`，10 秒内不通过则自动回滚。

### 方式 B：手工部署

```bash
# 1) 放文件
sudo mkdir -p /home/deployer/yolo_web_compare
# 拷贝 server.py index.html yolo11n.pt start.sh deploy.sh systemd/ 等
sudo chown -R deployer:deployer /home/deployer/yolo_web_compare

# 2) 装 unit
sudo cp systemd/yolo-compare.service /etc/systemd/system/
sudo systemctl daemon-reload

# 3) 启动 + 自启
sudo systemctl enable --now yolo-compare

# 4) 验证
systemctl is-active yolo-compare      # active
curl -s http://127.0.0.1:1239/api/health
```

### 可选配置

```bash
# /home/deployer/yolo_web_compare/compare.env
WC_RTMP_URL=rtmp://<RTMP_HOST>:1935/live/<STREAM_KEY>
WC_MAX_SAVED=50
WC_INTERVAL=0.8
```

在 unit 里加上 `EnvironmentFile=-/home/deployer/yolo_web_compare/compare.env` 即生效。

## 3. systemd 托管

`/etc/systemd/system/yolo-compare.service`：

```ini
[Unit]
Description=Pedestrian Detection Web Console (YOLO11, port 1239)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=deployer
Group=deployer
WorkingDirectory=/home/deployer/yolo_web_compare
Environment=PYTHONUNBUFFERED=1
ExecStart=/home/deployer/miniconda3/envs/yolo11/bin/python -u /home/deployer/yolo_web_compare/server.py
StandardOutput=append:/home/deployer/yolo_web_compare/service.log
StandardError=append:/home/deployer/yolo_web_compare/service.log
Restart=always
RestartSec=5
TimeoutStopSec=15

[Install]
WantedBy=multi-user.target
```

要点：

- **必须用 `append:`** 而不是 `>>` 重定向：非 append 写入 + `truncate` 清日志会造成“表观几十 G、实际 4K”的稀疏文件（本项目踩过，见第 8 节）。
- `Restart=always` + `RestartSec=5`：崩溃 5 秒后自动拉起。
- 不依赖 1238（`yolo-service`）：本服务自带模型推理，两个服务互不影响，可单独启停。

常用命令：

```bash
sudo systemctl status yolo-compare
sudo systemctl restart yolo-compare
sudo systemctl is-enabled yolo-compare       # enabled = 开机自启
journalctl -u yolo-compare -n 100 --no-pager
tail -f /home/deployer/yolo_web_compare/service.log
```

## 4. 日志轮转

已配置 `/etc/logrotate.d/yolo-compare`（与旧服务同策略）：

```
/home/deployer/yolo_web_compare/service.log {
    weekly
    maxsize 100M
    rotate 4
    compress
    delaycompress
    missingok
    notifempty
    copytruncate
    su deployer deployer
}
```

> `su` 指令不能省：logrotate 3.19 会因“父目录权限不安全”拒绝轮转该文件。

手动验证：`sudo logrotate -d /etc/logrotate.d/yolo-compare`（`-d` 为演练模式，不实际执行）。

## 5. 图库与磁盘

| 项 | 说明 |
|---|---|
| 目录 | `/home/deployer/yolo_web_compare/detections/` |
| `latest.jpg` | 最新检测画面，每轮覆盖（存在则说明连续检测在跑） |
| `detect_*.jpg` | 检出人的存档，文件名含时间戳与人数 |
| 上限 | `WC_MAX_SAVED`（默认 **50** 张 = 5 页 × 10 张），超出自动删最旧；服务启动时也会清理一次 |
| 分页 | 页面每页 10 张，最多 5 页；磁盘占用约 5MB |
| 保留策略 | 只留最近 50 张，更早的自动淘汰，需要长期留档请另行备份 |

手动清理（只保留最近 10 张）：

```bash
cd /home/deployer/yolo_web_compare/detections
ls -t detect_*.jpg | tail -n +11 | xargs -r rm -f
```

## 6. 验收清单

```bash
# 1) 服务与自启
systemctl is-enabled yolo-compare && systemctl is-active yolo-compare

# 2) 健康检查（版本 / 设备 / 连续检测状态 / 图库张数）
curl -s http://127.0.0.1:1239/api/health | python3 -m json.tool

# 3) 页面
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:1239/     # 200

# 3.1) 图库分页
curl -s 'http://127.0.0.1:1239/api/rtmp/gallery?page=1&size=10' | head -c 300

# 4) RTMP 流探测
curl -s http://127.0.0.1:1239/api/rtmp/status
# {"online":true,"video":{"codec":"h264","width":1280,"height":720,...}}

# 5) 抓帧
curl -s -o /tmp/s.jpg -w '%{http_code} %{size_download}\n' \
     http://127.0.0.1:1239/api/rtmp/snapshot

# 6) 连续检测跑 10 秒看帧率
curl -s -X POST http://127.0.0.1:1239/api/rtmp/autodetect/start
sleep 10 && curl -s http://127.0.0.1:1239/api/rtmp/autodetect/state
curl -s -X POST http://127.0.0.1:1239/api/rtmp/autodetect/stop

# 7) 单图检测
curl -s -F file=@/path/to/test.jpg http://127.0.0.1:1239/api/direct | head -c 200

# 8) 图库分页（第 1 页、每页 10 张）
curl -s 'http://127.0.0.1:1239/api/rtmp/gallery?page=1&size=10' | python3 -m json.tool | head -20
```

浏览器验收：打开 `http://<SERVER_IP>:1239/` → 依次点「检测流状态」「开始直播预览」「开始连续检测」，确认画面刷新、人数/帧率变化、图库出现缩略图。

## 7. 升级与回滚

```bash
# 升级（新包覆盖旧目录，保留 detections/ 与日志）
tar -xzf yolo_web_compare-1.1.1.tar.gz && cd yolo_web_compare-1.1.1
bash deploy.sh

# 回滚到上一个版本
ls -lt /home/deployer/yolo_web_compare/backup/
bash deploy.sh --rollback backup/compare-20261029_1030.tar.gz
```

`deploy.sh` 覆盖时使用 `cp -a`（不删除 `detections/`、`service.log`），只替换代码与静态文件。

## 8. 排错速查

| 现象 | 原因 / 处理 |
|---|---|
| 页面打不开 | `systemctl status yolo-compare`；`ss -lntp \| grep 1239` 看端口是否被旧进程占用 |
| 启动即退出 | `journalctl -u yolo-compare -n 50`；常见为 `yolo11n.pt` 缺失、cv2/torch 导入失败 |
| `/api/rtmp/status` 返回 `online:false` | 摄像头未推流 / 1935 端口不通；先在服务器上 `ffprobe <url>` 复现 |
| 抓帧报 `Option stimeout not found` | Ubuntu 22.04 的 ffmpeg 4.4.2 不支持该参数——本版已改用外层 `timeout` 控制，别再手动加回 |
| 抓帧报 `Connection refused / I/O error` | 流离线或拉流超时（默认 15 秒），页面会显示“拉流异常，自动重试” |
| 帧率只有 0.5~1 fps | **正常**：每轮重新建连调 ffmpeg，检测本身仅 8~10ms。需要更高帧率须改常驻拉流 |
| 连续检测启动不了 / 一直“已在运行” | 单实例限制，先 `POST /api/rtmp/autodetect/stop`；若进程重启过则状态已复位，可直接 start |
| 图库一直为空 | 画面里确实没人（`count=0` 不存档）；或 `WC_DETECT_DIR` 不可写（`ls -ld detections`） |
| 单图检测报 400 `bad image` | 上传的不是图片或图片损坏；确认 `accept` 类型与文件本身可解码 |
| `/api/direct` 超时 | GPU 被其他进程占满（`nvidia-smi`）；模型加载失败时启动日志会有 traceback |
| 日志文件“表观巨大、实际很小” | systemd 用了 `>>` 而非 `append:`，或对正在追加的文件做了 `truncate`。改 unit 为 `append:` 后重启即可 |
| `cuda: false` / `CUDNN_STATUS_NOT_INITIALIZED` | `pip show torch` 必须 `2.5.1+cu121`；`site-packages/nvidia` 里混入 cu13 库需清空后重装 |

## 9. 安全建议

- 1239 是**内网工具页**，无鉴权，不要映射到公网；如需外网访问，前面加 Nginx + Basic Auth。
- `url` 参数只允许 `rtmp://`（已做校验），但仍建议在防火墙层限制可访问的推流网段。
- 服务以 `deployer` 普通用户运行，不要改成 root。

## 10. 端口与目录速查

```
端口   1237 旧服务   1238 YOLO 接口服务   1239 检测控制台
单元   pedestron-legacy / yolo-service / yolo-compare
目录   /home/deployer/Pedestron/demo      （旧服务）
       /home/deployer/yolo_service        （新服务）
       /home/deployer/yolo_web_compare    （本工具）
日志   …/demo/log/b_service.log / …/yolo_service/service.log / …/yolo_web_compare/service.log
图库   /home/deployer/yolo_web_compare/detections/
备份   /home/deployer/yolo_web_compare/backup/ 与 /backup/deployer/
```
