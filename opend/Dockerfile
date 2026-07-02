# OpenD gateway container — Ubuntu 22.04 base with headless-VNC bootstrap layer.
#
# Design: the OpenD binaries live in a volume (mounted at /data), not baked into
# the image. This means version upgrades don't require rebuilding, and the image
# stays small (~250 MB).
#
# The container runs a three-stage entrypoint:
#   1. Download OpenD if /data version stamp doesn't match $OPEND_VERSION
#   2. If no valid fingerprint, start Xvfb + x11vnc + noVNC + OpenD-GUI (AppImage)
#      on :6080 so the user can log in from a browser. Once the fingerprint is
#      written, kill the VNC stack.
#   3. exec the headless CLI FutuOpenD binary.

FROM ubuntu:22.04

ENV DEBIAN_FRONTEND=noninteractive

# Two dependency groups:
#   - OpenD runtime libs (needed by both CLI and GUI)
#   - Bootstrap-only libs (Xvfb + VNC + AppImage extraction). These are loaded
#     into RAM only during Stage 2, then pkill'd; the disk cost is ~180 MB.
RUN apt-get update && apt-get install -y --no-install-recommends \
        ca-certificates wget curl tar \
        libx11-6 libxext6 libxrender1 libxi6 libxrandr2 libxfixes3 libxcursor1 \
        libxcomposite1 libxdamage1 libxtst6 libxkbcommon0 libxkbcommon-x11-0 \
        libglib2.0-0 libgtk-3-0 libnss3 libdbus-1-3 libfontconfig1 libfreetype6 \
        libgl1 libglx-mesa0 libegl1 libgbm1 libasound2 libpulse0 \
        libssl3 libcurl4 \
        xvfb x11vnc fluxbox novnc websockify \
        fuse libfuse2 \
        procps psmisc net-tools netcat-openbsd \
    && rm -rf /var/lib/apt/lists/*

# Symlink noVNC index for cleaner URL
RUN ln -sf /usr/share/novnc/vnc.html /usr/share/novnc/index.html || true

COPY entrypoint.sh /usr/local/bin/entrypoint.sh
COPY bootstrap-vnc.sh /usr/local/bin/bootstrap-vnc.sh
COPY render-config.sh /usr/local/bin/render-config.sh
RUN chmod +x /usr/local/bin/entrypoint.sh \
             /usr/local/bin/bootstrap-vnc.sh \
             /usr/local/bin/render-config.sh

# /data holds the OpenD binaries + userdata + fingerprint (persistent volume)
# /config holds the rendered FutuOpenD.xml (persistent volume)
VOLUME ["/data", "/config"]

EXPOSE 11111 6080

ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
