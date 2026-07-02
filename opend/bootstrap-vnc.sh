#!/bin/bash
# VNC bootstrap layer — only runs when a fresh OpenD login is required.
#
# Starts Xvfb + fluxbox + x11vnc + noVNC/websockify, then launches the OpenD
# GUI AppImage. The AppImage writes the device fingerprint into /data/userdata
# once the user completes login + SMS verification in the browser.
#
# entrypoint.sh polls for the fingerprint file and pkills this whole layer
# once it appears.

set -e

DATA=/data
export DISPLAY=:99
export HOME=/data
export XDG_RUNTIME_DIR=/tmp/xdg
mkdir -p "$XDG_RUNTIME_DIR" && chmod 700 "$XDG_RUNTIME_DIR"

VNC_PW=$(cat "$DATA/.vnc_password")

echo "[bootstrap] starting Xvfb :99 (1280x720)"
Xvfb :99 -screen 0 1280x720x24 -nolisten tcp &
sleep 2

echo "[bootstrap] starting fluxbox"
fluxbox >/dev/null 2>&1 &
sleep 1

# x11vnc — bind to localhost only, websockify bridges it to :6080
echo "[bootstrap] starting x11vnc"
x11vnc -display :99 -passwd "$VNC_PW" \
       -forever -shared -bg -o /tmp/x11vnc.log \
       -localhost -rfbport 5900

echo "[bootstrap] starting noVNC on :6080"
websockify --web=/usr/share/novnc 6080 localhost:5900 &

sleep 2

echo "[bootstrap] launching OpenD-GUI (AppImage)"
if [ ! -x "$DATA/FutuOpenD-GUI.AppImage" ]; then
    echo "[bootstrap] ERROR: $DATA/FutuOpenD-GUI.AppImage not found or not executable"
    exit 1
fi

# --appimage-extract-and-run avoids the FUSE requirement (Docker containers
# usually can't mount FUSE without --privileged).
cd "$DATA"
"$DATA/FutuOpenD-GUI.AppImage" --appimage-extract-and-run &
GUI_PID=$!

echo "[bootstrap] all services up, GUI pid=$GUI_PID"
echo "[bootstrap] waiting for user to complete login in browser..."

# Keep the script alive so entrypoint.sh can kill it as a group
wait
