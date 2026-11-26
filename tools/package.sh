#!/bin/bash
# 打包 yolo_web_compare：生成 dist/yolo_web_compare-<ver>.tar.gz + .sha256
# 用法：bash tools/package.sh
set -eu
SRC="$(cd "$(dirname "$0")/.." && pwd)"
VER="$(tr -d ' \r\n' < "$SRC/VERSION")"
NAME="yolo_web_compare-$VER"
OUT="$SRC/dist"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

mkdir -p "$OUT"
mkdir -p "$TMP/$NAME"
cd "$SRC"

# 只打源码与文档，排除运行期产物
for f in server.py index.html start.sh deploy.sh requirements.txt README.md DEPLOY.md CHANGELOG.md VERSION .gitignore yolo11n.pt; do
    [ -f "$f" ] && cp -a "$f" "$TMP/$NAME/"
done
cp -a systemd "$TMP/$NAME/"
mkdir -p "$TMP/$NAME/tools"
cp -a tools/package.sh "$TMP/$NAME/tools/"
mkdir -p "$TMP/$NAME/detections"   # 空目录占位

# 文件清单
( cd "$TMP/$NAME" && find . -type f ! -name MANIFEST.sha256 -print0 \
    | sort -z | xargs -0 sha256sum > MANIFEST.sha256 )

tar -czf "$OUT/$NAME.tar.gz" -C "$TMP" "$NAME"
( cd "$OUT" && sha256sum "$NAME.tar.gz" > "$NAME.tar.gz.sha256" )

echo "已生成："
ls -lh "$OUT/$NAME.tar.gz" "$OUT/$NAME.tar.gz.sha256"
echo
echo "包内文件："
tar -tzf "$OUT/$NAME.tar.gz" | sed 's/^/  /'
