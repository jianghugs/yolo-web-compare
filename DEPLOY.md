# yolo_web_compare — Deployment Guide

> 🌐 Language: **English** | [简体中文](DEPLOY.zh-CN.md)

## 1. Environment

| Item | Requirement |
|---|---|
| OS | Ubuntu 20.04+ (tested on 22.04) |
| GPU | NVIDIA (recommended); CPU-only works with slower fps |
| Python | 3.10 (conda env recommended) |
| System packages | **ffmpeg** (frame grabbing), ffprobe (stream probe) |

## 2. Install from release package

```bash
tar -xzf yolo_web_compare-1.3.0.tar.gz && cd yolo_web_compare-1.3.0
sha256sum -c MANIFEST.sha256
bash deploy.sh        # unit + logrotate + start + health check, auto-rollback
```

Manual: copy files, edit `systemd/yolo-compare.service` paths, then:

```bash
sudo cp systemd/yolo-compare.service /etc/systemd/system/
sudo systemctl daemon-reload && sudo systemctl enable --now yolo-compare
```

## 3. systemd unit essentials

```ini
[Service]
WorkingDirectory=/home/deployer/yolo_web_compare
ExecStart=/home/deployer/miniconda3/envs/yolo11/bin/python server.py
Restart=always
RestartSec=5
StandardOutput=append:/home/deployer/yolo_web_compare/service.log
```

The service is **independent** of yolo_service (1238) unless
`WC_DETECT_VIA` is configured.

## 4. Docker

```bash
docker build -t yolo-web-compare .
docker run -d --gpus all -p 1239:1239 \
  -e WC_RTMP_URL=rtmp://<RTMP_HOST>:1935/live/<STREAM_KEY> \
  yolo-web-compare
```

## 5. Memory / VRAM

| Mode | VRAM | Notes |
|---|---|---|
| Local model (default) | ~2.5GB | second YOLO instance besides yolo_service |
| `WC_DETECT_VIA=http://127.0.0.1:1238` | ~0GB | forwards to yolo_service `/api/detectPreview` |

## 6. Verification checklist

```bash
systemctl is-active yolo-compare
curl -s http://127.0.0.1:1239/api/health | python3 -m json.tool
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:1239/
curl -s 'http://127.0.0.1:1239/api/rtmp/gallery?page=1&size=10' | head -c 300
python -m pytest tests/ -q
```

## 7. Upgrade & rollback

`deploy.sh` stops the service, backs up to `backup/compare-<ts>.tar.gz`,
overwrites (preserving `detections/` and logs), restarts and health-checks;
on failure it rolls back automatically. Manual: `bash deploy.sh --rollback backup/<ts>.tar.gz`.

## 8. Gallery disk management

- Cap: `WC_MAX_SAVED` (default **50** frames ≈ 5MB); startup prunes the
  excess once.
- Manual cleanup (keep newest 10):

```bash
cd /home/deployer/yolo_web_compare/detections
ls -t detect_*.jpg | tail -n +11 | xargs -r rm -f
```

## 9. Troubleshooting

| Symptom | Cause / fix |
|---|---|
| `ffmpeg: command not found` | install ffmpeg (`apt install ffmpeg`) |
| Snapshot returns 502 | stream offline — check camera push status; autodetect keeps retrying with backoff |
| autodetect stuck at "waiting for frames" | grabber reconnecting; check `state.error` and that the RTMP URL is reachable |
| fps stuck at ~1 | verify the persistent grabber started (log line); older versions opened a connection per frame |
| Page loads but no detection | `nvidia-smi` for VRAM; if `WC_DETECT_VIA` set, check yolo_service `/api/health` |
| Gallery doesn't grow | dedup active (`WC_SAVE_HASH_DIST`) or conf too low — raise `WC_CONF` down? lower it |

## 10. Security notes

- LAN-only by design, no auth; put behind a proxy with auth if exposed.
- Stream URLs restricted to `rtmp://` (SSRF guard); gallery filenames are
  HTML-escaped and URL-encoded in the page.
