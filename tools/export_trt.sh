#!/usr/bin/env bash
# 导出 TensorRT engine 加速推理（可选优化，需 GPU + tensorrt 环境）。
# 用法：bash tools/export_trt.sh   → 生成 yolo11n.engine
# 之后把 server.py 中模型路径改为 yolo11n.engine 重启即可。
#
# 注意：engine 与显卡强绑定，换卡需要重新导出。
set -euo pipefail
cd "$(dirname "$0")/.."

python - <<'EOF'
from ultralytics import YOLO
model = YOLO('yolo11n.pt')
model.export(format='engine', half=True, device=0, imgsz=640)
print('export done -> yolo11n.engine')
EOF
