# Morning Handoff — OpenD Docker Deployment

**Session:** 2026-07-01 → 2026-07-02
**Status:** Ready for you to complete first-time OpenD login in the browser.

## What was done overnight

Four commits on `main` (not pushed — your call):

```
19a9d13  Update OpenD spec: mark items verified after smoke test
cd648f0  Align futu_client.py with official Futu API skill guidance
c4ecc81  Add OpenD Docker service with VNC bootstrap flow
3c30ccc  Add OpenD Docker deployment design spec
```

### 1. Design spec
`docs/superpowers/specs/2026-07-02-opend-docker-design.md`
Full architecture, memory budget, self-healing flow, YAGNI list.

### 2. Full implementation in `opend/`
```
opend/
├── Dockerfile          # Ubuntu 22.04 + Xvfb + x11vnc + noVNC + fluxbox
├── entrypoint.sh       # 3-stage: version check → VNC bootstrap → CLI exec
├── bootstrap-vnc.sh    # Launches Xvfb + noVNC + OpenD-GUI AppImage
├── render-config.sh    # Generates FutuOpenD.xml from env vars
└── .dockerignore
```

### 3. Compose stack
`docker-compose.yml` rewritten to run two services on a shared bridge network:
- `tracker` on `:5000` — Flask, `FUTU_HOST=opend` env var wired in
- `opend` on `:11111` (internal only) + `:6080` (bootstrap only)
- Two named volumes: `opend-data` (binaries + fingerprint) and `opend-config` (rendered XML)

### 4. API client alignment
`futu_client.py` rewritten per the official Futu API skill pack
(`https://openapi.futunn.com/futu-api-doc/intro/ai.html`):
- One `OpenSecTradeContext` per SecurityFirm with `filter_trdmarket=TrdMarket.NONE`
- Client-side account matching against `trdmarket_auth`
- `acc_id` preserved as int (18-digit precision)
- Explicit `currency=` on `accinfo_query`
- SG/JP/MY/CA market support added

`requirements.txt`: `futu-api>=9.0` → `futu-api>=10.4.6408` (skill pack minimum).

### 5. Docs
- `docs/opend-setup.md` — end-user first-login walkthrough
- `README.md` — updated Docker section
- `.env.example` — annotated template

## Smoke test verified

I ran a full `docker compose build` + `up -d opend` with a dummy account:

```
[opend] using pre-seeded tarball at /data/_seed.tgz
[opend] CLI dir: ./Futu_OpenD_10.8.6808_Ubuntu18.04/Futu_OpenD_10.8.6808_Ubuntu18.04
[opend] GUI dir: ./Futu_OpenD_10.8.6808_Ubuntu18.04/Futu_OpenD-GUI_10.8.6808_Ubuntu18.04
[opend] OpenD 10.8.6808 installed to /data
[opend] rendered config to /config/FutuOpenD.xml
[opend] NO VALID FINGERPRINT — launching VNC bootstrap
[opend]   Open a browser to:  http://<this-server>:6080/vnc.html
[opend]   VNC password:       eLbLGLR2vd67fzkRd9Gt
```

`ps aux` inside the container confirmed:
- `Xvfb :99` running
- `fluxbox` running
- `x11vnc` on 5900 (localhost only)
- `websockify` bridging 6080 → 5900
- `/data/FutuOpenD-GUI.AppImage --appimage-extract-and-run` running

`curl -sI http://localhost:6080/vnc.html` returned `HTTP/1.1 200 OK`.

The smoke test was torn down before commit; state is clean.

## What YOU need to do to finish

### 1. Set your real credentials
```bash
cd /home/eli/futu-tracker
cp .env.example .env
# Edit .env — at minimum:
#   FUTU_LOGIN_ACCOUNT=<your Futu ID / phone / email>
#   FLASK_SECRET_KEY=$(openssl rand -hex 32)
```

### 2. Launch
```bash
docker compose up -d
docker compose logs -f opend
```

Wait ~1–2 minutes for the OpenD tarball download (463 MB, only on first run).
The logs will end with:
```
Open a browser to:  http://<this-server>:6080/vnc.html
VNC password:       <20 random chars>
```

### 3. Complete the OpenD login (this is the human step I can't do for you)
1. Browser → `http://<server-ip>:6080/vnc.html`
2. Paste the VNC password shown in logs
3. In the OpenD GUI window: enter your Futu password → SMS/email code
4. Check "trust this device", finish login
5. Wait for the logs to show `fingerprint acquired ... starting headless CLI FutuOpenD`

### 4. Point tracker at OpenD
1. `http://<server-ip>:5000` → login as `admin` / `admin123`
2. Admin panel → OpenD config → set `futu_host = opend`, `futu_port = 11111`
3. Save → click "Sync now"
4. **Change the admin password.**

### 5. Push when happy
```bash
git log origin/main..HEAD  # review the 4 commits + the 2 pre-existing ones
git push origin main
```

## Known gotchas

- **The 6080 port is exposed on 0.0.0.0 by default.** For a public server,
  either `VNC_BIND=127.0.0.1` in `.env` and SSH-tunnel `ssh -L 6080:localhost:6080`,
  or keep the random password (it's the only auth on that port).
- **The `admin` / `admin123` default is still there** — I did NOT change it
  because the design doc explicitly listed it as "don't touch user accounts".
  Change it as soon as you log in the first time.
- **If OpenD login fails** with "device untrusted" in a loop, that's normal —
  the fingerprint file is written by the OpenD backend after the SMS code
  is accepted, not after you type the password. Complete the whole SMS flow.
- **First-run download is ~463 MB.** If your server has bad connectivity,
  download the tarball locally and copy it to the volume before `docker compose up`:
  ```bash
  docker volume create futu-tracker_opend-data
  docker run --rm -v futu-tracker_opend-data:/data -v $PWD:/src alpine \
    cp /src/Futu_OpenD_10.8.6808_Ubuntu18.04.tar.gz /data/_seed.tgz
  ```

## What was NOT touched

- `data/portfolio.db` — untouched
- `data/.encryption_key` — untouched
- `admin` password — still default
- Nothing pushed to remote

Enjoy your coffee ☕
