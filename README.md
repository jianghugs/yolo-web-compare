# yolo_web_compare — Pedestrian Detection Web Console

> 🌐 Language: **English** | [简体中文](README.zh-CN.md)

A single-page web console for pedestrian detection built on Ultralytics **YOLO11n**
(port **1239**):

1. **RTMP live preview** — grabs frames from a camera RTMP stream and polls them in the
   browser as a "pseudo live view" (~1–1.5 fps);
2. **continuous stream detection** — a background thread grabs a frame every ~0.8s, runs
   YOLO, refreshes the annotated view, and **auto-archives any frame with people**;
3. **detection gallery** — archived frames shown newest-first, paginated (10 per page,
   at most 5 pages / 50 images), with people-count and time badges, click to enlarge;
4. **single-image detection** — upload one image and get the annotated image + count +
   latency; nothing is archived, no callbacks.

> Since v1.2.0 the comparison against the legacy Pedestron service (1237) has been
> **removed**; the page only serves YOLO detection.

---

## 1. Architecture

```
Browser ──HTTP──> server.py (Flask, :1239)
                  ├── /api/rtmp/snapshot    → ffmpeg grabs a frame → JPEG
                  ├── /api/rtmp/status      → ffprobe stream info
                  ├── /api/rtmp/autodetect  → background thread: grab → YOLO → archive
                  ├── /api/direct           → single-image YOLO detection
                  └── /api/rtmp/gallery     → listings of detections/
                                   │
                                   ├── detections/latest.jpg (latest annotated frame)
                                   └── detections/detect_<time>_<count>p.jpg (gallery)
```

- Model: `yolo11n.pt` (COCO pretrained, `classes=[0]` person only), `cuda:0` when a GPU is
  available, otherwise CPU.
- Frame grabbing: the system `ffmpeg` binary (Ubuntu 22.04 ships 4.4.2); each grab is an
  independent connection with the timeout enforced by an outer `timeout` command.
- Concurrency: grab results are cached for 0.8s behind a lock, so many browser viewers only
  trigger one stream grab.

## 2. Layout

```
yolo_web_compare/
├── server.py                 # the whole backend
├── index.html                # the page (single file, no external deps)
├── yolo11n.pt                # model weights (5.6MB)
├── start.sh                  # foreground start script
├── deploy.sh                 # one-shot deploy (backup + health check + auto rollback)
├── systemd/
│   ├── yolo-compare.service  # this service (1239)
│   └── yolo-compare.logrotate
├── tools/package.sh          # build dist/yolo_web_compare-<ver>.tar.gz
├── detections/               # runtime: annotated frames
├── dist/                     # build artifacts
├── README.md / DEPLOY.md / CHANGELOG.md / VERSION
└── requirements.txt
```

## 3. API

| Method | Path | Params | Description |
|---|---|---|---|
| GET | `/` | — | the page |
| GET | `/api/health` | — | version, device, port, autodetect state, gallery size |
| POST | `/api/direct` | `file` | single-image detection, returns `count / elapsed_ms / device / image(base64)` |
| GET | `/api/rtmp/status` | `url` (optional) | ffprobe: `online / video / audio / format` |
| GET | `/api/rtmp/snapshot` | `url` (optional) | current frame as JPEG (`Cache-Control: no-store`) |
| POST | `/api/rtmp/detect` | `url` | grab frame + one-shot YOLO detection |
| POST | `/api/rtmp/autodetect/start` | `url` | start the background continuous detection |
| POST | `/api/rtmp/autodetect/stop` | — | stop it |
| GET | `/api/rtmp/autodetect/state` | — | `running / count / elapsed_ms / fps / frames / saved / error` |
| GET | `/api/rtmp/gallery` | `page`, `size` | paginated gallery (10/page default), returns `items / total / pages / page / size` |
| GET | `/detections/<file>` | — | view a detection image |

Examples:

```bash
curl -s http://127.0.0.1:1239/api/health

curl -s -X POST -F "file=@test.jpg" http://127.0.0.1:1239/api/direct

curl -s "http://127.0.0.1:1239/api/rtmp/status?url=rtmp://<RTMP_HOST>:1935/live/xxx"

curl -s -X POST -d "url=rtmp://<RTMP_HOST>:1935/live/xxx" \
     http://127.0.0.1:1239/api/rtmp/autodetect/start
sleep 5 && curl -s http://127.0.0.1:1239/api/rtmp/autodetect/state
curl -s -X POST http://127.0.0.1:1239/api/rtmp/autodetect/stop
```

## 4. Environment variables

| Variable | Default | Description |
|---|---|---|
| `WC_PORT` | `1239` | listen port |
| `WC_RTMP_URL` | `rtmp://<RTMP_HOST>:1935/live/<STREAM_KEY>` | default stream URL (editable on the page) |
| `WC_CONF` | `0.5` | confidence threshold |
| `WC_INTERVAL` | `0.8` | per-round interval of continuous detection (seconds) |
| `WC_DETECT_DIR` | `<project>/detections` | directory for annotated frames |
| `WC_MAX_SAVED` | `50` | max archived frames (5 pages x 10), oldest removed beyond that; also enforced once at startup |
| `WC_PAGE_SIZE` | `10` | gallery page size |

For systemd deployment write them into `compare.env` (one `KEY=VALUE` per line) and load it
in the unit via `EnvironmentFile=-`.

## 5. Quick start

```bash
# on the server (conda env already has torch + ultralytics + flask + opencv)
cd /home/deployer/yolo_web_compare
bash start.sh                      # foreground, Ctrl+C to stop

# or via systemd
sudo systemctl restart yolo-compare
sudo systemctl status yolo-compare --no-pager
```

Open `http://<SERVER_IP>:1239/` in a browser.

## 6. Using the page

1. **Live view**: confirm the stream URL → click "开始直播预览" (Start live preview); the
   left pane shows the LIVE feed (grab failures are reported and polling stops).
2. **Start detection**: click "开始连续检测" (Start continuous detection); the right pane
   shows the annotated view, and the status line updates `people | round latency | fps |
   frames | archived`.
3. **Gallery**: frames with people land in the gallery below, **10 per page** — use
   prev/next or page numbers; auto-refreshes every 10s, manual refresh button available;
   click a thumbnail to view the original.
4. **Single image**: drag an image in → "开始检测" (Detect) → annotated image + metrics.

## 7. Packaging & distribution

```bash
bash tools/package.sh
# dist/yolo_web_compare-1.2.1.tar.gz (+ .sha256), with a per-file MANIFEST.sha256 inside
```

## 8. Known limitations

- **~1 fps ceiling**: each round creates a new ffmpeg connection (handshake + one frame
  takes 1–2s) — that is the bottleneck, detection itself is only 8–10ms. For 5–10 fps, run
  a resident ffmpeg process and take the latest frame instead.
- The gallery parses the people count from the filename (`detect_<time>_<N>p.jpg`);
  manual renames make it show `-1`.
- Only the latest 50 frames (5 pages) are kept; older ones are auto-deleted. Sync elsewhere
  if you need long-term retention.
- Frame grabbing depends on the system `ffmpeg`; without it the endpoints return 502.
- When the stream drops, the detection thread keeps retrying and reports the error in the
  status; the picture resumes once the camera pushes again.
