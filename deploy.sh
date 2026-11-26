#!/bin/bash
# yolo_web_compare 一键部署 / 升级 / 回滚
#   bash deploy.sh                  # 部署当前目录到 /home/deployer/yolo_web_compare 并重启
#   bash deploy.sh --rollback <tar> # 用 backup 目录里的备份包回滚
set -u

APP_DIR="/home/deployer/yolo_web_compare"
UNIT="yolo-compare"
PY="/home/deployer/miniconda3/envs/yolo11/bin/python"
SRC="$(cd "$(dirname "$0")" && pwd)"
STAMP="$(date +%Y%m%d_%H%M%S)"
HEALTH="http://127.0.0.1:1239/api/health"

log()  { echo -e "\033[32m[deploy]\033[0m $*"; }
warn() { echo -e "\033[33m[warn  ]\033[0m $*"; }
die()  { echo -e "\033[31m[error ]\033[0m $*" >&2; exit 1; }

need_sudo() {
    if [ "$(id -u)" -ne 0 ]; then
        sudo -v || die "需要 sudo 权限"
        SUDO="sudo"
    else
        SUDO=""
    fi
}

health_check() {
    for i in $(seq 1 10); do
        if curl -sf "$HEALTH" >/dev/null 2>&1; then
            log "健康检查通过：$(curl -s $HEALTH | head -c 160)"
            return 0
        fi
        sleep 1
    done
    return 1
}

install_unit() {
    if [ -f "$APP_DIR/systemd/$UNIT.service" ]; then
        $SUDO cp "$APP_DIR/systemd/$UNIT.service" "/etc/systemd/system/$UNIT.service"
        $SUDO systemctl daemon-reload
    fi
    if [ -f "$APP_DIR/systemd/$UNIT.logrotate" ]; then
        $SUDO cp "$APP_DIR/systemd/$UNIT.logrotate" "/etc/logrotate.d/$UNIT"
    fi
    $SUDO systemctl enable "$UNIT" >/dev/null 2>&1 || true
}

# ---------- 回滚 ----------
if [ "${1:-}" = "--rollback" ]; then
    PKG="${2:-}"
    [ -f "$PKG" ] || die "回滚包不存在：$PKG"
    need_sudo
    log "回滚到 $PKG"
    $SUDO systemctl stop "$UNIT" || true
    tar -xzf "$PKG" -C "$APP_DIR" --strip-components=0 2>/dev/null || tar -xzf "$PKG" -C "$APP_DIR"
    install_unit
    $SUDO systemctl start "$UNIT"
    health_check || die "回滚后健康检查仍失败，请查看 journalctl -u $UNIT -n 80"
    exit 0
fi

# ---------- 部署 ----------
need_sudo
[ -f "$SRC/server.py" ] || die "当前目录没有 server.py，请在项目目录内执行"
[ -f "$SRC/index.html" ] || die "当前目录没有 index.html"

log "部署源：$SRC"
log "目标：$APP_DIR"

# 1) 备份现有目录（保留 detections/ 与日志，只备份代码与静态文件）
if [ -d "$APP_DIR" ] && [ -f "$APP_DIR/server.py" ]; then
    mkdir -p "$APP_DIR/backup"
    BAK="$APP_DIR/backup/compare-$STAMP.tar.gz"
    tar -czf "$BAK" -C "$APP_DIR" \
        --exclude=detections --exclude=service.log --exclude=backup \
        server.py index.html deploy.sh README.md DEPLOY.md CHANGELOG.md VERSION 2>/dev/null
    log "已备份：$BAK"
else
    BAK=""
    log "首次部署，无需备份"
fi

# 2) 停服务，覆盖文件（不动 detections/ 与 service.log）
$SUDO systemctl stop "$UNIT" 2>/dev/null || true
mkdir -p "$APP_DIR"
for f in server.py index.html deploy.sh README.md DEPLOY.md CHANGELOG.md VERSION requirements.txt start.sh; do
    [ -f "$SRC/$f" ] && cp -f "$SRC/$f" "$APP_DIR/"
done
rm -rf "$APP_DIR/systemd"
cp -a "$SRC/systemd" "$APP_DIR/"
[ -f "$SRC/yolo11n.pt" ] && cp -f "$SRC/yolo11n.pt" "$APP_DIR/"
[ -x "$APP_DIR/start.sh" ] || chmod +x "$APP_DIR/start.sh"
[ -x "$APP_DIR/deploy.sh" ] || chmod +x "$APP_DIR/deploy.sh"
chown -R deployer:deployer "$APP_DIR" 2>/dev/null || true
mkdir -p "$APP_DIR/detections"

# 3) 依赖检查
[ -x "$PY" ] || die "Python 不存在：$PY"
command -v ffmpeg >/dev/null 2>&1 || warn "未找到 ffmpeg，RTMP 功能不可用：sudo apt install -y ffmpeg"
$PY -c "import flask, cv2, requests, ultralytics" 2>/dev/null || die "Python 依赖缺失（flask/cv2/requests/ultralytics）"
$PY -m py_compile "$APP_DIR/server.py" || die "server.py 语法检查失败"

# 4) 安装 unit 并启动
install_unit
$SUDO systemctl start "$UNIT"

# 5) 健康检查，失败自动回滚
if health_check; then
    log "部署完成 ✅  页面：http://$(hostname -I | awk '{print $1}'):1239/"
    $SUDO systemctl --no-pager --lines=3 status "$UNIT" | tail -4
else
    warn "健康检查未通过，尝试回滚"
    if [ -n "$BAK" ] && [ -f "$BAK" ]; then
        $SUDO systemctl stop "$UNIT" || true
        tar -xzf "$BAK" -C "$APP_DIR"
        $SUDO systemctl start "$UNIT"
        sleep 3
        if health_check; then
            die "已回滚到部署前版本，请检查日志：journalctl -u $UNIT -n 80"
        fi
    fi
    die "部署失败且回滚未成功，请手动排查：journalctl -u $UNIT -n 80"
fi
