# yolo_web_compare — Deployment & Operations Manual

> 🌐 Language: **English** | [简体中文](DEPLOY.zh-CN.md)

> Target machine: `<SERVER_IP>` (Ubuntu 22.04), run as user `deployer`
> Covers: dependencies → first deployment → systemd → acceptance → upgrade/rollback → troubleshooting.

## 0. Services on this machine

| Port | systemd unit | Purpose | Environment |
|---|---|---|---|
| 1237 | `pedestron-legacy` | legacy Pedestron pedestrian detection (talks to the alert platform) | `/home/deployer/miniconda3/envs/open-mmlab` |
| 1238 | `yolo-service` | YOLO11 detection API service (protocol-compatible with the legacy one) | `/home/deployer/miniconda3/envs/yolo11` |
| 1239 | `yolo-compare` | **this tool**: detection web console (RTMP live view + continuous detection + gallery + single-image) | `/home/deployer/miniconda3/envs/yolo11` |

Coupling: 1239 is fully standalone — it never calls 1237/1238, only local `ffmpeg` for
frame grabs plus local YOLO inference.

## 1. Requirements

| Item | Requirement | Check |
|---|---|---|
| OS | Ubuntu 22.04 (or a distro with the same glibc) | `lsb_release -a` |
| Python | 3.10 (conda env `yolo11`) | `conda env list` |
| Python packages | torch 2.5.1+cu121, ultralytics, flask, opencv | `python -c "import torch,ultralytics,cv2;print(torch.cuda.is_available())"` |
| System tools | **ffmpeg / ffprobe >= 4.4** (required for grabbing) | `ffmpeg -version \| head -1` |
| GPU | optional; falls back to CPU automatically (`DEVICE='cpu'`) | `nvidia-smi` |
| Disk | gallery keeps 50 frames x ~90KB ≈ 5MB; reserve >= 1GB to be safe | `df -h /home` |

Install system dependencies (if ffmpeg is missing):

```bash
sudo apt update && sudo apt install -y ffmpeg
```

Python dependencies (`requirements.txt`): normally already present in the `yolo11` env;
if missing:

```bash
/home/deployer/miniconda3/envs/yolo11/bin/pip install -r requirements.txt \
    -i https://mirrors.aliyun.com/pypi/simple/
```

> ⚠️ Never `pip install -U torch` in this environment. It must stay `torch 2.5.1+cu121`;
> a `+cu13*` build leads to `CUDNN_STATUS_NOT_INITIALIZED` (see section 8).

## 2. First deployment

### Option A: package deployment (recommended)

```bash
# 1) upload and verify
scp yolo_web_compare-1.2.1.tar.gz* deployer@<SERVER_IP>:~/
sha256sum -c yolo_web_compare-1.2.1.tar.gz.sha256

# 2) extract
tar -xzf yolo_web_compare-1.2.1.tar.gz && cd yolo_web_compare-1.2.1

# 3) one-shot deploy (stops the service -> backs up to backup/ -> overwrites files
#    -> installs the unit -> starts -> health check)
bash deploy.sh
```

Key behaviours of `deploy.sh`:

- detects a missing `yolo11n.pt` and restores it from the package or an old directory;
- packs the existing directory into `backup/compare-<timestamp>.tar.gz` before overwriting;
- installs/refreshes `/etc/systemd/system/yolo-compare.service` via `sudo -S`;
- after starting, polls `http://127.0.0.1:1239/api/health` and rolls back automatically
  if it does not pass within 10 seconds.

### Option B: manual deployment

```bash
# 1) place files
sudo mkdir -p /home/deployer/yolo_web_compare
# copy server.py index.html yolo11n.pt start.sh deploy.sh systemd/ etc.
sudo chown -R deployer:deployer /home/deployer/yolo_web_compare

# 2) install the unit
sudo cp systemd/yolo-compare.service /etc/systemd/system/
sudo systemctl daemon-reload

# 3) enable + start
sudo systemctl enable --now yolo-compare

# 4) verify
systemctl is-active yolo-compare      # active
curl -s http://127.0.0.1:1239/api/health
```

### Optional configuration

```bash
# /home/deployer/yolo_web_compare/compare.env
WC_RTMP_URL=rtmp://<RTMP_HOST>:1935/live/<STREAM_KEY>
WC_MAX_SAVED=50
WC_INTERVAL=0.8
```

Add `EnvironmentFile=-/home/deployer/yolo_web_compare/compare.env` to the unit to load it.

## 3. systemd

`/etc/systemd/system/yolo-compare.service`:

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

Key points:

- **Use `append:`**, not `>>` redirection: non-append writes combined with `truncate`
  produce sparse files that look like tens of GB but occupy 4K (we hit this; see section 8).
- `Restart=always` + `RestartSec=5`: auto-restart 5s after a crash.
- No dependency on 1238 (`yolo-service`): this service runs its own model; both can be
  started/stopped independently.

Common commands:

```bash
sudo systemctl status yolo-compare
sudo systemctl restart yolo-compare
sudo systemctl is-enabled yolo-compare       # enabled = starts on boot
journalctl -u yolo-compare -n 100 --no-pager
tail -f /home/deployer/yolo_web_compare/service.log
```

## 4. Log rotation

`/etc/logrotate.d/yolo-compare` (same policy as the legacy service):

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

> The `su` directive is mandatory: logrotate 3.19 refuses to rotate the file otherwise
> ("parent directory has insecure permissions").

Manual check: `sudo logrotate -d /etc/logrotate.d/yolo-compare` (`-d` = dry run).

## 5. Gallery & disk

| Item | Notes |
|---|---|
| Directory | `/home/deployer/yolo_web_compare/detections/` |
| `latest.jpg` | latest annotated frame, overwritten each round (its presence means continuous detection is running) |
| `detect_*.jpg` | archives of frames with people; filename contains timestamp and count |
| Cap | `WC_MAX_SAVED` (default **50** = 5 pages x 10), oldest removed beyond that; also enforced once at startup |
| Pagination | 10 per page on the page, at most 5 pages; ~5MB on disk |
| Retention | only the newest 50 frames are kept; sync elsewhere for long-term archives |

Manual cleanup (keep the newest 10 only):

```bash
cd /home/deployer/yolo_web_compare/detections
ls -t detect_*.jpg | tail -n +11 | xargs -r rm -f
```

## 6. Acceptance checklist

```bash
# 1) service & boot start
systemctl is-enabled yolo-compare && systemctl is-active yolo-compare

# 2) health (version / device / autodetect state / gallery size)
curl -s http://127.0.0.1:1239/api/health | python3 -m json.tool

# 3) page
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:1239/     # 200

# 3.1) gallery pagination
curl -s 'http://127.0.0.1:1239/api/rtmp/gallery?page=1&size=10' | head -c 300

# 4) RTMP stream probe
curl -s http://127.0.0.1:1239/api/rtmp/status
# {"online":true,"video":{"codec":"h264","width":1280,"height":720,...}}

# 5) frame grab
curl -s -o /tmp/s.jpg -w '%{http_code} %{size_download}\n' \
     http://127.0.0.1:1239/api/rtmp/snapshot

# 6) run continuous detection for 10s and check fps
curl -s -X POST http://127.0.0.1:1239/api/rtmp/autodetect/start
sleep 10 && curl -s http://127.0.0.1:1239/api/rtmp/autodetect/state
curl -s -X POST http://127.0.0.1:1239/api/rtmp/autodetect/stop

# 7) single-image detection
curl -s -F file=@/path/to/test.jpg http://127.0.0.1:1239/api/direct | head -c 200

# 8) gallery pagination (page 1, 10 per page)
curl -s 'http://127.0.0.1:1239/api/rtmp/gallery?page=1&size=10' | python3 -m json.tool | head -20
```

Browser acceptance: open `http://<SERVER_IP>:1239/` → click "检测流状态" (probe stream),
"开始直播预览" (live preview), "开始连续检测" (continuous detection) in order; confirm the
picture refreshes, the count/fps change, and thumbnails appear in the gallery.

## 7. Upgrade & rollback

```bash
# upgrade (the new package overwrites code, keeps detections/ and logs)
tar -xzf yolo_web_compare-1.1.1.tar.gz && cd yolo_web_compare-1.1.1
bash deploy.sh

# roll back to the previous version
ls -lt /home/deployer/yolo_web_compare/backup/
bash deploy.sh --rollback backup/compare-20261029_1030.tar.gz
```

`deploy.sh` copies with `cp -a` (it never deletes `detections/` or `service.log`), only
replacing code and static files.

## 8. Troubleshooting

| Symptom | Cause / fix |
|---|---|
| Page unreachable | `systemctl status yolo-compare`; `ss -lntp \| grep 1239` to see if an old process holds the port |
| Exits immediately | `journalctl -u yolo-compare -n 50`; commonly missing `yolo11n.pt` or cv2/torch import failure |
| `/api/rtmp/status` returns `online:false` | camera not pushing / port 1935 unreachable; reproduce with `ffprobe <url>` on the server |
| Grab error `Option stimeout not found` | Ubuntu 22.04's ffmpeg 4.4.2 does not support that option — this version uses an outer `timeout` instead; do not add it back |
| Grab error `Connection refused / I/O error` | stream offline or grab timeout (default 15s); the page shows "stream error, retrying" |
| Only 0.5–1 fps | **normal**: each round reconnects ffmpeg; detection itself is 8–10ms. Higher fps needs a resident grab process |
| Cannot start / stuck at "already running" | single-instance limit; `POST /api/rtmp/autodetect/stop` first; if the process restarted, the state was reset — just start again |
| Gallery always empty | nobody in frame (`count=0` is not archived); or `WC_DETECT_DIR` not writable (`ls -ld detections`) |
| Single-image detection returns 400 `bad image` | not an image or a corrupt file; check the file itself decodes |
| `/api/direct` times out | GPU saturated by another process (`nvidia-smi`); model load failures print a traceback in the start log |
| Log file looks huge but is tiny | unit used `>>` instead of `append:`, or `truncate` ran on an appending file. Switch to `append:` and restart |
| `cuda: false` / `CUDNN_STATUS_NOT_INITIALIZED` | `pip show torch` must be `2.5.1+cu121`; clear cu13 libs from `site-packages/nvidia` and reinstall |

## 9. Security notes

- 1239 is an **intranet tool page** without authentication — do not expose it to the
  internet; put Nginx + Basic Auth in front if you must.
- The `url` parameter only accepts `rtmp://` (validated), but also restrict reachable
  streaming subnets at the firewall.
- The service runs as the unprivileged `deployer` user — do not change it to root.

## 10. Ports & directories quick reference

```
Ports    1237 legacy   1238 YOLO API service   1239 web console
Units    pedestron-legacy / yolo-service / yolo-compare
Dirs     /home/deployer/Pedestron/demo      (legacy)
         /home/deployer/yolo_service        (API service)
         /home/deployer/yolo_web_compare    (this tool)
Logs     .../demo/log/b_service.log / .../yolo_service/service.log / .../yolo_web_compare/service.log
Gallery  /home/deployer/yolo_web_compare/detections/
Backups  /home/deployer/yolo_web_compare/backup/ and /backup/deployer/
```
