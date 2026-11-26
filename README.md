# yolo_web_compare — Pedestrian Detection Web Console

> 🌐 Language: **English** | [简体中文](README.zh-CN.md)

A single-purpose web console for **Ultralytics YOLO11** pedestrian
detection: RTMP live preview, continuous stream detection with an archived
gallery, and one-shot single-image testing.

```
RTMP camera ──► StreamGrabber (persistent ffmpeg, reconnect w/ backoff)
                   ├─► /api/rtmp/snapshot   live preview (poll)
                   └─► autodetect thread ──► YOLO11 ──► detections/ gallery
upload image ────────────────► /api/direct  ──► annotated result
```

## 1. Highlights (v1.3.0)

- **Persistent stream grabber**: one long-lived `ffmpeg` process decodes the
  stream continuously; frames go from ~1 fps (new connection per frame) to
  **5–15 fps**, with exponential-backoff reconnect (1s → 30s)
- **Global inference lock**: HTTP requests and the background detector
  thread share the model safely
- **Gallery save-dedup**: a detection is only archived if its dHash differs
  from the previous archive (`WC_SAVE_HASH_DIST`, default 10) — static
  scenes no longer flood the gallery
- **Optional remote inference** (`WC_DETECT_VIA`): forward frames to
  yolo_service's `/api/detectPreview` and skip loading a second model
  (saves ~2.5GB VRAM)
- **Optional webhook alerts** (`WC_ALERT_WEBHOOK`): POST a JSON
  notification whenever a person is detected
- Paginated gallery (10/page, 5 pages max), XSS-safe rendering, single
  image drag-and-drop testing

## 2. Layout

```
yolo_web_compare/
├── server.py        # Flask app + StreamGrabber + autodetect thread
├── utils.py         # pure helpers (URL validation, dHash) — unit tested
├── index.html       # single-page console
├── yolo11n.pt       # weights (only loaded when WC_DETECT_VIA is unset)
├── detections/      # archived annotated frames (latest.jpg + detect_*.jpg)
├── tests/           # pytest unit tests
├── tools/           # packaging / TensorRT export
├── systemd/         # unit file + logrotate
└── deploy.sh        # backup → overwrite → verify, auto-rollback
```

## 3. API

| Method | Path | Params | Description |
|---|---|---|---|
| GET | `/api/health` | — | version, device, port, autodetect state, gallery size |
| POST | `/api/direct` | `file` | single-image detection → `count / elapsed_ms / device / image(base64)` |
| GET | `/api/rtmp/status` | `url` (opt) | ffprobe: `online / video / audio / format` |
| GET | `/api/rtmp/snapshot` | `url` (opt) | current frame as JPEG |
| POST | `/api/rtmp/detect` | `url` | grab frame + one-shot detection |
| POST | `/api/rtmp/autodetect/start` | `url` | start continuous detection |
| POST | `/api/rtmp/autodetect/stop` | — | stop it |
| GET | `/api/rtmp/autodetect/state` | — | `running / count / elapsed_ms / fps / frames / saved / error` |
| GET | `/api/rtmp/gallery` | `page`, `size` | paginated gallery → `items / total / pages / page / size` |
| GET | `/detections/<file>` | — | archived image |

## 4. Environment variables

| Variable | Default | Description |
|---|---|---|
| `WC_PORT` | `1239` | listen port |
| `WC_RTMP_URL` | `rtmp://<RTMP_HOST>:1935/live/<STREAM_KEY>` | default stream URL |
| `WC_CONF` | `0.5` | confidence threshold |
| `WC_INTERVAL` | `0.8` | detection round interval (seconds) |
| `WC_DETECT_DIR` | `./detections` | archive directory |
| `WC_MAX_SAVED` | `50` | max archived frames (5 pages × 10) |
| `WC_PAGE_SIZE` | `10` | gallery page size |
| `WC_SAVE_HASH_DIST` | `10` | dHash distance ≤ ⇒ skip archiving (dedup) |
| `WC_DETECT_VIA` | *(empty)* | e.g. `http://127.0.0.1:1238` — forward inference, don't load a model here |
| `WC_ALERT_WEBHOOK` | *(empty)* | POST JSON `{count, ts, file, source[, image]}` on detection |
| `WC_ALERT_IMAGE` | `0` | include base64 annotated image in webhook payload |

## 5. Quick start

```bash
pip install -r requirements.txt      # torch cu121 wheels first, see file header
python server.py                     # http://0.0.0.0:1239

# Docker (bundles ffmpeg)
docker build -t yolo-web-compare .
docker run -d --gpus all -p 1239:1239 -e WC_RTMP_URL=rtmp://host/live/key yolo-web-compare

# tests
python -m pytest tests/ -q
```

## 6. Using the page

1. **Stream status** — probe codec/resolution/audio of the RTMP stream.
2. **Live preview** — server-side frame polling (~2 fps).
3. **Continuous detection** — start/stop the background detector; status
   line shows count / latency / fps / archived. Every frame with a person
   is archived (deduped) into the gallery below.
4. **Gallery** — paginated 10/page, oldest auto-evicted at 50 frames.
5. **Single image** — drag & drop → instant annotated result.

## 7. Packaging

```bash
bash tools/package.sh    # → dist/yolo_web_compare-<ver>.tar.gz + .sha256 + MANIFEST
```

## 8. Known limitations

- Gallery keeps only the latest 50 frames; archive elsewhere for long-term
  retention.
- Live preview is frame polling (~2 fps), not a real-time protocol; for
  smooth video add a WebRTC/HLS gateway.
- Only `rtmp://` URLs are accepted (SSRF guard).

## 9. License

MIT — see [LICENSE](LICENSE).
