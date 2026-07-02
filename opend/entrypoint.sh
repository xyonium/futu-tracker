#!/bin/bash
# OpenD container entrypoint — three-stage state machine.
#
# Stage 1: version check + download
# Stage 2: fingerprint check + VNC bootstrap if missing
# Stage 3: exec headless CLI FutuOpenD
#
# All state lives in two volumes:
#   /data    — OpenD binaries, userdata (incl. fingerprint), VNC password
#   /config  — rendered FutuOpenD.xml

set -e

DATA=/data
CFG=/config/FutuOpenD.xml
TARGET_VERSION="${OPEND_VERSION:-10.8.6808}"
OPEND_DL_URL="${OPEND_DOWNLOAD_URL:-https://www.futunn.com/download/fetch-lasted-link?name=opend-ubuntu}"

log() { echo "[opend][$(date -u +%H:%M:%S)] $*" >&2; }

# ───── Utilities ────────────────────────────────────────────────────────

fingerprint_valid() {
    # OpenD stores the device fingerprint under one of a few possible paths.
    # We probe the whole userdata tree; if any .dat file with "SnFinger",
    # "device", or "AppData" in its name is non-empty, we consider the
    # container "logged in".
    #
    # First-boot heuristic: after the first successful VNC bootstrap, the
    # entrypoint records which files were newly created into
    # /data/.fingerprint_paths — subsequent starts check exactly those.
    if [ -f "$DATA/.fingerprint_paths" ]; then
        while IFS= read -r p; do
            [ -f "$p" ] && [ -s "$p" ] || return 1
        done < "$DATA/.fingerprint_paths"
        return 0
    fi
    # Fallback probe (before first bootstrap has been done).
    # OpenD 10.8 stores session under /data/.com.futunn.FutuOpenD/ (not
    # userdata/), so we scan the whole /data tree.
    if find "$DATA" -maxdepth 6 -type f \
         \( -iname 'SnFinger*' -o -iname 'Device.dat' -o -iname 'AppData*' \) \
         -size +0c 2>/dev/null | grep -q .; then
        return 0
    fi
    return 1
}

ensure_vnc_password() {
    if [ -n "${VNC_PASSWORD:-}" ]; then
        echo -n "$VNC_PASSWORD" > "$DATA/.vnc_password"
    elif [ ! -f "$DATA/.vnc_password" ]; then
        # Generate a 20-char random password once, persist across restarts
        head -c 15 /dev/urandom | base64 | tr -d '=+/' | head -c 20 > "$DATA/.vnc_password"
    fi
    chmod 600 "$DATA/.vnc_password"
}

# ───── Stage 1: version check + download ────────────────────────────────

install_opend() {
    log "installing OpenD version $TARGET_VERSION..."
    rm -rf "$DATA/_dl" && mkdir -p "$DATA/_dl"
    cd "$DATA/_dl"

    # Fast path: pre-seeded tarball at /data/_seed.tgz (smoke tests,
    # air-gapped installs, or after a failed extract). Skips the network.
    if [ -f "$DATA/_seed.tgz" ]; then
        log "using pre-seeded tarball at /data/_seed.tgz"
        cp "$DATA/_seed.tgz" opend.tar.gz
    else
        log "downloading from $OPEND_DL_URL"
        # Follow redirects; the "fetch-lasted-link" endpoint 302s to the current
        # tarball. If OPEND_DOWNLOAD_URL is set to a direct URL, that's used instead.
        # -nv keeps a single completion line per URL instead of dot-progress spam,
        # which drowns the entrypoint's own log messages in `docker logs`.
        if ! wget -nv -O opend.tar.gz "$OPEND_DL_URL"; then
            log "ERROR: download failed"
            exit 1
        fi
    fi

    tar -xzf opend.tar.gz
    # Tarball layout (verified against 10.8.6808):
    #   Futu_OpenD_<ver>_Ubuntu<osver>/                <- $top
    #     ├── README.txt
    #     ├── Futu_OpenD-GUI_<ver>_Ubuntu<osver>/     <- $gui_dir (has .AppImage)
    #     └── Futu_OpenD_<ver>_Ubuntu<osver>/         <- $cli_dir (has FutuOpenD)
    #
    # Note: the outer and inner CLI dirs share the same name prefix, so we
    # need to explicitly skip the outer dir itself (mindepth 1) and skip the
    # GUI sibling (grep -v GUI).
    local top cli_dir gui_dir
    top=$(find . -mindepth 1 -maxdepth 1 -type d -name 'Futu_OpenD_*' | head -1)
    cli_dir=$(find "$top" -mindepth 1 -maxdepth 1 -type d -name 'Futu_OpenD_*' \
              | grep -v 'GUI' | head -1)
    gui_dir=$(find "$top" -mindepth 1 -maxdepth 1 -type d -name 'Futu_OpenD-GUI_*' | head -1)

    if [ -z "$cli_dir" ] || [ ! -x "$cli_dir/FutuOpenD" ]; then
        log "ERROR: unexpected tarball layout — no CLI dir found in $top"
        find "$top" -maxdepth 2 -type d | while read -r d; do log "  $d"; done
        exit 1
    fi
    log "CLI dir: $cli_dir"
    log "GUI dir: ${gui_dir:-none}"

    # Move CLI + libs into /data root
    log "installing CLI binaries..."
    mv "$cli_dir"/* "$DATA/"
    chmod +x "$DATA/FutuOpenD" "$DATA/FTUpdate" 2>/dev/null || true

    # Move GUI AppImage (used only during bootstrap)
    if [ -n "$gui_dir" ]; then
        local appimg
        appimg=$(find "$gui_dir" -maxdepth 2 -name '*.AppImage' | head -1)
        if [ -n "$appimg" ]; then
            cp "$appimg" "$DATA/FutuOpenD-GUI.AppImage"
            chmod +x "$DATA/FutuOpenD-GUI.AppImage"
        fi
    fi

    cd /
    rm -rf "$DATA/_dl"
    mkdir -p "$DATA/userdata"
    echo "$TARGET_VERSION" > "$DATA/.installed_version"
    log "OpenD $TARGET_VERSION installed to $DATA"
}

mkdir -p "$DATA/userdata" /config
INSTALLED="$(cat "$DATA/.installed_version" 2>/dev/null || echo none)"

if [ "$INSTALLED" != "$TARGET_VERSION" ]; then
    log "installed version: $INSTALLED, target: $TARGET_VERSION — downloading"
    install_opend
    # Version bump usually invalidates the fingerprint; force a fresh bootstrap
    rm -f "$DATA/.fingerprint_paths"
    find "$DATA" -maxdepth 6 -type f \
         \( -iname 'SnFinger*' -o -iname 'Device.dat' -o -iname 'AppData*' \) \
         -delete 2>/dev/null || true
else
    log "OpenD $TARGET_VERSION already installed"
fi

# ───── Render config ────────────────────────────────────────────────────

# Auto-generate RSA keypair on first boot (needed when OpenD listens on 0.0.0.0
# — the SDK requires encryption for non-localhost connections).
if [ ! -f "$DATA/rsa_private.pem" ]; then
    log "generating RSA keypair..."
    openssl genrsa -out "$DATA/rsa_private.pem" 2048 2>/dev/null
    openssl rsa -in "$DATA/rsa_private.pem" -pubout -out "$DATA/rsa_public.pem" 2>/dev/null
    chmod 600 "$DATA/rsa_private.pem"
fi

/usr/local/bin/render-config.sh > "$CFG"
log "rendered config to $CFG"

# ───── Stage 2: fingerprint check + bootstrap ───────────────────────────

if ! fingerprint_valid; then
    log "════════════════════════════════════════════════════════════════"
    log "NO VALID FINGERPRINT — launching VNC bootstrap"
    log ""
    ensure_vnc_password
    VNC_PW=$(cat "$DATA/.vnc_password")
    log "  Open a browser to:  http://<this-server>:6080/vnc.html"
    log "  VNC password:       $VNC_PW"
    log ""
    log "  Log into OpenD in the browser window. When you see 'connected'"
    log "  and the account list appears, this container will detect the"
    log "  fingerprint and switch to headless mode automatically."
    log "════════════════════════════════════════════════════════════════"

    # Take a snapshot of userdata before bootstrap so we can diff afterwards
    BEFORE=$(mktemp)
    find "$DATA/userdata" -type f 2>/dev/null | sort > "$BEFORE"

    /usr/local/bin/bootstrap-vnc.sh &
    BS_PID=$!

    # Poll for the fingerprint every 5 seconds. Timeout after 30 minutes
    # so a stalled bootstrap doesn't hang the container forever.
    TIMEOUT_SEC=1800
    ELAPSED=0
    while ! fingerprint_valid; do
        sleep 5
        ELAPSED=$((ELAPSED + 5))
        if ! kill -0 $BS_PID 2>/dev/null; then
            log "ERROR: bootstrap VNC process died — check logs above"
            exit 1
        fi
        if [ $ELAPSED -ge $TIMEOUT_SEC ]; then
            log "ERROR: bootstrap timed out after $TIMEOUT_SEC seconds"
            kill $BS_PID 2>/dev/null || true
            exit 1
        fi
        if [ $((ELAPSED % 60)) -eq 0 ]; then
            log "still waiting for login (${ELAPSED}s / ${TIMEOUT_SEC}s)..."
        fi
    done

    log "fingerprint acquired — recording paths for future startups"
    AFTER=$(mktemp)
    find "$DATA/userdata" -type f 2>/dev/null | sort > "$AFTER"
    # Any file that appeared during bootstrap and matches known name patterns
    # becomes a persistent "fingerprint checkpoint" checked on every start.
    comm -13 "$BEFORE" "$AFTER" | grep -iE 'SnFinger|device|AppData' \
        > "$DATA/.fingerprint_paths" || true
    if [ ! -s "$DATA/.fingerprint_paths" ]; then
        # Fall back to whatever files were created
        comm -13 "$BEFORE" "$AFTER" > "$DATA/.fingerprint_paths"
    fi
    log "checkpoint files:"
    while IFS= read -r p; do log "  $p"; done < "$DATA/.fingerprint_paths"
    rm -f "$BEFORE" "$AFTER"

    log "shutting down VNC layer to reclaim memory..."
    kill $BS_PID 2>/dev/null || true
    pkill -f 'Xvfb'          2>/dev/null || true
    pkill -f 'x11vnc'        2>/dev/null || true
    pkill -f 'websockify'    2>/dev/null || true
    pkill -f 'fluxbox'       2>/dev/null || true
    pkill -f 'FutuOpenD-GUI' 2>/dev/null || true
    pkill -f '\.AppImage'    2>/dev/null || true
    sleep 3
else
    log "valid fingerprint found, skipping VNC bootstrap"
fi

# ───── Stage 3: production CLI ──────────────────────────────────────────

cd "$DATA"
log "starting headless CLI FutuOpenD on port 11111"
exec "$DATA/FutuOpenD" -cfg_file="$CFG"
