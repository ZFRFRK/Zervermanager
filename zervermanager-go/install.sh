#!/bin/sh
# zervermanager-go — fresh Debian 11/12/13 installer (goplan M7 pulled forward).
#
# Layout on the target:
#   /opt/zervermanager/zervermanager      binary
#   /opt/zervermanager/web_config/        UI assets (served unchanged)
#   /etc/zervermanager/auth.json          admin account (0600)
#   /var/lib/zervermanager/               runtime state (0700)
#   /etc/systemd/system/zervermanager.service
#
# Usage (at the repo root, as root — `sudo ./install.sh`):
#   sudo ./install.sh                — prompts for the admin password, serves :8090
#   sudo ./install.sh 'pw'           — sets it non-interactively
#   sudo ./install.sh --reset 'pw'   — re-set an existing admin password
#
# Shadow options (goplan §6: read-only prober beside the live python):
#   sudo ./install.sh --port 8091 --shadow 'pw'
#
# Repo layout expected by this script:
#   bin/zervermanager-linux-<arch>   prebuilt binaries (committed)
#   web_config/                      UI assets (in the repo)
#
# If no prebuilt binary matches and the Go toolchain IS present, the
# script builds from source instead — dev convenience, never required.
set -eu

APPDIR=/opt/zervermanager
AUTHFILE=/etc/zervermanager/auth.json
UNITNAME=zervermanager.service
ADDR=:8090
FLAGS=""

while [ "$#" -gt 0 ]; do
    case "$1" in
        --reset)  RESET=1; shift ;;
        --port)   ADDR=":$2"; shift 2 ;;
        --shadow) FLAGS="-shadow"; SHADOW_LABEL=" (shadow: read-only)"; UNITNAME=zervermanager-go-shadow.service; shift ;;
        *)        PW="$1"; shift ;;
    esac
done
RESET="${RESET:-0}"
PW="${PW:-}"
UNIT="/etc/systemd/system/$UNITNAME"

HERE="$(cd "$(dirname "$0")" && pwd)"

# pick the prebuilt binary for this machine
case "$(uname -m)" in
    x86_64)  ARCH="amd64"  ;;
    aarch64) ARCH="arm64"  ;;
    *) echo "unsupported architecture: $(uname -m)"; exit 1 ;;
esac
BIN=""
for cand in "$HERE/bin/zervermanager-linux-$ARCH" "$HERE/zervermanager-linux-$ARCH" "$HERE/zervermanager"; do
    [ -f "$cand" ] && BIN="$cand" && break
done
if [ -z "$BIN" ] && command -v go >/dev/null 2>&1; then
    echo "no prebuilt linux/$ARCH binary found — building from source..."
    (cd "$HERE" && GOFLAGS=-mod=mod go build -o "$HERE/bin/zervermanager-linux-$ARCH" ./cmd/zervermanager)
    BIN="$HERE/bin/zervermanager-linux-$ARCH"
fi
[ -n "$BIN" ] || { echo "no prebuilt linux/$ARCH binary in bin/ (and no go toolchain) — see README"; exit 1; }

STATEDIR=/var/lib/zervermanager
mkdir -p "$APPDIR"

# Stop the old service before copying: replacing a running binary fails
# with "Text file busy" (Linux refuses to modify an executable that is
# currently mapped). The shadow install shares the same APPDIR binary,
# so the main unit must be stopped for it too. Fresh installs have no
# unit yet.
WAS_RUNNING=0
if systemctl is-active --quiet zervermanager.service 2>/dev/null; then
    WAS_RUNNING=1
    systemctl stop zervermanager.service
fi
if systemctl is-active --quiet zervermanager-go-shadow.service 2>/dev/null; then
    systemctl stop zervermanager-go-shadow.service
fi

cp "$BIN" "$APPDIR/zervermanager"
chmod 755 "$APPDIR/zervermanager"

# UI assets: one source of truth. Repo layout puts web_config/ at the
# repo root (shared with the python version); a standalone tree keeps it
# beside the installer; a live python install is the last fallback.
if [ -f "$HERE/web_config/index.html" ]; then
    cp -a "$HERE/web_config" "$APPDIR/web_config"
elif [ -f "$HERE/../web_config/index.html" ]; then
    cp -a "$HERE/../web_config" "$APPDIR/web_config"
elif [ -d "/home/a2/zervermanager1/web_config" ]; then
    cp -a "/home/a2/zervermanager1/web_config" "$APPDIR/web_config"
fi
[ -f "$APPDIR/web_config/index.html" ] || { echo "web_config/index.html not found (repo root, beside installer, or /home/*/zervermanager1)"; exit 1; }

mkdir -p /etc/zervermanager "$STATEDIR"
chmod 700 /etc/zervermanager "$STATEDIR"

if [ "$RESET" = "1" ] || [ ! -f "$AUTHFILE" ]; then
    if [ -z "$PW" ]; then
        printf "Set admin password: "
        stty -echo 2>/dev/null || true
        read -r PW
        stty echo 2>/dev/null || true
        echo
    fi
    "$APPDIR/zervermanager" -init-password "$PW"
    [ -f "$AUTHFILE" ] || { echo "auth init failed"; exit 1; }
    PW=""
fi

cat > "$UNIT" <<EOF
[Unit]
Description=Zervermanager (Go${SHADOW_LABEL:-})
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
ExecStart=/opt/zervermanager/zervermanager -addr $ADDR -web-dir /opt/zervermanager/web_config $FLAGS
Restart=on-failure
RestartSec=2
# privileged operations (apt, systemctl, bind, nginx...) are the product
User=root

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable "$UNITNAME" 2>/dev/null || true
systemctl restart "$UNITNAME"

# A shadow install briefly stopped the main manager for the binary copy —
# bring it back if it was running.
if [ "$UNITNAME" = "zervermanager-go-shadow.service" ] && [ "$WAS_RUNNING" = "1" ]; then
    systemctl start zervermanager.service
fi

PORT="${ADDR#*:}"
echo
echo "installed. UI:  http://$(hostname -I | awk '{print $1}'):${PORT}  (${UNITNAME}${FLAGS:+, shadow: read-only})"
echo "logs:  journalctl -u ${UNITNAME%.service} -f"
