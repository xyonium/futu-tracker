# OpenD Docker 部署设计（含 VNC 自愈引导）

**Date:** 2026-07-02
**Status:** Draft, pending user review
**Target OS:** Ubuntu 22.04 (host and containers)
**OpenD Version:** 10.8.6808 (as of 2026-06-24 release)

---

## 1. 目标

让 futu-tracker 项目在**一台内存紧张的 Ubuntu 22.04 服务器**上，通过 `docker compose up` 一键部署包含 Futu OpenD 网关 + Flask Web 站点的完整栈。特殊约束：

- OpenD **首次登录需人肉过验证码**，服务器无 GUI —— 通过临时启动的 noVNC 网页解决
- 内存紧张 —— 常驻服务尽量瘦，GUI 只在需要时占内存
- 自愈 —— 版本升级、指纹过期、卷被清都能触发正确的引导流程，无需运维介入
- OpenD 二进制 463 MB —— 不重复下载

## 2. 架构

```
┌────────────────── docker-compose.yml ──────────────────┐
│                                                         │
│   [tracker]   ─── Flask + gunicorn,  :5000              │
│                    ~80 MB 常驻                          │
│                    depends_on: opend (service_started)  │
│                    ENV: FUTU_HOST=opend, FUTU_PORT=11111│
│                                                         │
│   [opend]     ─── 单容器多阶段 entrypoint               │
│                    Stage 1: version check + download    │
│                    Stage 2: fingerprint check           │
│                             有  → skip                  │
│                             无  → 起 VNC + AppImage GUI │
│                                    等指纹落盘 → 杀 GUI  │
│                    Stage 3: exec CLI FutuOpenD (常驻)   │
│                    :11111 → 内网                        │
│                    :6080  → 仅 bootstrap 阶段监听       │
│                    ~150 MB 常驻，bootstrap 时 +500 MB   │
│                                                         │
│   共享 volume: opend-data (二进制 + 指纹 + 日志)        │
│   共享 volume: opend-config (FutuOpenD.xml)             │
└─────────────────────────────────────────────────────────┘
```

**两个服务，不是三个。** Init 和 bootstrap 都是**同一个 opend 容器的 entrypoint 阶段**，跑完自动进入下一阶段，最终 `exec` 到 CLI OpenD。这样：
- 只维护一份 Docker 层
- 常驻状态就一个进程（`FutuOpenD` CLI）
- Bootstrap 期间的 Xvfb / x11vnc / noVNC / GUI 进程在指纹落盘后被 `pkill`，内存立即回收
- Tracker 独立是因为 Python 环境和 OpenD 完全无关，混一起没意义

## 3. Volume 布局

```
opend-data/                          # docker volume
├── .installed_version               # "10.8.6808" —— 版本戳
├── FutuOpenD                        # CLI 二进制（约 60 MB）
├── FutuOpenD-GUI.AppImage           # GUI 二进制（约 200 MB，只 bootstrap 用）
├── lib*.so                          # 运行时库
├── Languages/                       # 语言资源
└── userdata/                        # OpenD 运行时写入的目录
    ├── SnFingerPrint.dat            # 设备指纹（关键！）
    ├── logs/
    └── .../                         # OpenD 自建子目录

opend-config/
└── FutuOpenD.xml                    # 从模板生成，含 login_account / api_port 等
```

**关键设计决策**：
- 二进制放 volume，**不放镜像**。镜像层只装 apt 依赖 + entrypoint 脚本。这样：
  - 镜像 ~200 MB（bootstrap + VNC 依赖）；OpenD 二进制升级不用重建镜像
  - Volume 里 `.installed_version` 决定要不要重新下载
- **指纹文件路径**：需要在部署时探测 OpenD 到底把它写到哪（可能是 `~/.futuopend/`, `./userdata/`, `./config/` 等）。方案是 bootstrap 结束前 `find` 一遍新增的文件，把候选路径写到 `.fingerprint_paths`，后续启动时检查这几个候选路径都还在，就算指纹有效。
- **AppImage 也放 volume**：这样"删指纹重新登录"时不用重下载 AppImage。

## 4. Entrypoint 状态机

`opend/entrypoint.sh` 的伪码：

```bash
#!/bin/bash
set -e
DATA=/data
CFG=/config/FutuOpenD.xml
TARGET_VERSION="${OPEND_VERSION:-10.8.6808}"

# ───── Stage 1: version check ─────
INSTALLED="$(cat $DATA/.installed_version 2>/dev/null || echo none)"
if [ "$INSTALLED" != "$TARGET_VERSION" ]; then
    log "version $INSTALLED → $TARGET_VERSION, downloading..."
    fetch_and_extract "$TARGET_VERSION"          # wget + tar + 精简（只留必要文件）
    echo "$TARGET_VERSION" > $DATA/.installed_version
    # 版本变了指纹很可能不兼容 → 强制重新引导
    rm -f $DATA/userdata/SnFingerPrint.dat
fi

# ───── Stage 2: bootstrap check ─────
if ! fingerprint_valid; then
    log "no valid fingerprint, launching VNC bootstrap on :6080"
    log "VNC password: (see /data/.vnc_password)"
    start_bootstrap_vnc &                        # 后台起 Xvfb + x11vnc + noVNC + AppImage
    BS_PID=$!
    wait_for_fingerprint                         # 轮询指纹文件出现
    log "fingerprint acquired, shutting down VNC layer"
    kill $BS_PID 2>/dev/null || true
    pkill -f 'Xvfb|x11vnc|websockify|FutuOpenD-GUI' || true
    sleep 3
fi

# ───── Stage 3: production CLI ─────
log "starting headless CLI FutuOpenD"
exec $DATA/FutuOpenD -cfg_file=$CFG
```

`fingerprint_valid` 的判断：
```bash
fingerprint_valid() {
    [ -f "$DATA/userdata/SnFingerPrint.dat" ] && \
    [ -s "$DATA/userdata/SnFingerPrint.dat" ]
    # 若首次部署摸清了 OpenD 会创建哪些文件，可加更多检查
}
```

## 5. Bootstrap VNC 层

`opend/bootstrap-vnc.sh`（只在 Stage 2 触发时运行）：

```bash
#!/bin/bash
export DISPLAY=:99
Xvfb :99 -screen 0 1280x720x24 &
sleep 1
fluxbox &                                        # 最小窗口管理器
x11vnc -display :99 -passwd "$(cat /data/.vnc_password)" \
       -forever -shared -rfbport 5900 &
websockify --web=/usr/share/novnc 6080 localhost:5900 &

# 直接把 AppImage 拉起来，不做额外解压
$DATA/FutuOpenD-GUI.AppImage --appimage-extract-and-run &
wait
```

VNC 密码：容器首次启动时随机生成一次，写到 `$DATA/.vnc_password`（volume 里持久化，方便下次 bootstrap 用同一个）。文件在 host 上是 `./opend-data/_data/.vnc_password`（bind mount 时）或需 `docker exec` 读取。为方便用户，entrypoint 会把密码同时打印到 stderr（`docker compose logs opend` 就能看到）。

⚠️ **密码打印在日志里的取舍**：日志被截获风险 vs 用户方便。我选择打印，因为：a) 服务器一般只有你自己有 shell；b) 密码只在 bootstrap 阶段有意义，指纹拿到后 VNC 端口就关了。可通过 `.env` 里设置 `VNC_PASSWORD` 覆盖随机生成，用户想用固定密码就自己设。

**端口暴露策略**（按用户选择）：
```yaml
ports:
  - "6080:6080"    # 公网暴露，靠密码保护
```
在 bootstrap 结束后，`pkill websockify` 会让 6080 端口没进程 listen，Docker 层 NAT 依然映射但内部无响应，等效于自动关闭。

## 6. 版本探测（回应用户提问：`--version` 行不行）

**验证结果**：`FutuOpenD --version` 会报错 `指定参数version错误`，直接启动过程中日志会打 `Futu OpenD版本信息: 10.8.6808(20260624193800)`，可以 grep 出来。**但这需要真的启动一次 OpenD**，成本高、不适合每次容器启动都做。

**采用方案**：文件戳记法。
- `.installed_version` 是 install 时**由 entrypoint 主动写入**的（内容就是 `$OPEND_VERSION` 环境变量）
- 无需运行 OpenD 就能读
- 100% 可靠

**未来增强**（可选）：加一个手动命令 `docker exec opend /usr/local/bin/probe-version.sh`，跑 OpenD 5 秒再 kill，从日志 grep 版本号做二次校验。日常不用。

## 7. 内存预算

| 状态 | tracker | opend | 合计 |
|---|---|---|---|
| 常驻运行 | ~80 MB | ~150 MB | **~230 MB** |
| Bootstrap 触发 | ~80 MB | ~600 MB (含 Xvfb+AppImage) | ~680 MB |
| 数据同步中 | ~100 MB | ~200 MB | ~300 MB |

镜像大小：
- `opend` 镜像 ~250 MB（Ubuntu 22.04 + Xvfb + x11vnc + noVNC + fluxbox + libX11 等）
- OpenD 二进制在 volume 里，不占镜像层
- Volume 空间：~300 MB（CLI 60 MB + AppImage 200 MB + 库 30 MB）

## 8. 文件清单（要创建/修改的）

新建：
- `opend/Dockerfile`
- `opend/entrypoint.sh`
- `opend/bootstrap-vnc.sh`
- `opend/FutuOpenD.xml.tmpl`
- `opend/.dockerignore`
- `.env.example`（如果没有）
- `docs/opend-setup.md`（用户操作手册）

修改：
- `docker-compose.yml` — 加 `opend` service，改 `tracker` 的 depends_on 和环境变量
- `.gitignore` — 忽略 `opend-data/`, `.env`
- `README.md` — 更新 Docker 部署段落

**不修改**：`app.py`, `futu_client.py`（现有代码已支持通过 `futu_host`/`futu_port` 配置项连 OpenD，只需在管理后台把 `futu_host` 从 `127.0.0.1` 改成 `opend`）。

## 9. 用户操作流程

**首次部署**：
```bash
git clone <repo>
cd futu-tracker
cp .env.example .env
# 编辑 .env，填 FUTU_LOGIN_ACCOUNT=<你的富途ID>
docker compose up -d
docker compose logs -f opend
# 看到 "VNC password: xxxxxx" 和 "launching VNC bootstrap on :6080"
# 浏览器打开 http://<server-ip>:6080/vnc.html
# 输入 VNC 密码 → 看到 OpenD GUI → 输账号密码 → 收手机验证码 → 登录
# 日志变成 "fingerprint acquired, shutting down VNC layer"
# 之后 "starting headless CLI FutuOpenD"
# 打开 http://<server-ip>:5000，admin/admin123 登录
# 管理后台 → OpenD 配置 → futu_host = "opend" (服务名), futu_port = 11111
# 首次同步
```

**版本升级**：
```bash
# 编辑 .env 里 OPEND_VERSION=<new>
docker compose up -d
# entrypoint 检测到版本不一致 → 下载新版 → 强制清指纹 → 重新走 bootstrap
```

**清指纹强制重登**：
```bash
docker compose exec opend rm /data/userdata/SnFingerPrint.dat
docker compose restart opend
```

**卷全清（灾难恢复）**：
```bash
docker compose down -v
docker compose up -d   # 重新走完整流程
```

## 10. 已知风险与未验证项

- ⚠️ **OpenD 指纹文件路径未 100% 确认** —— 需要 bootstrap 首跑时 `find $DATA -newer <marker>` 摸清。已在 entrypoint 里预留了 debug 模式：`OPEND_DEBUG=1` 会 dump 所有新增文件到日志。
- ⚠️ **AppImage 在 Docker 里依赖 FUSE** —— 用 `--appimage-extract-and-run` 绕开 FUSE 需求（AppImage 官方推荐做法）。
- ⚠️ **Ubuntu 18.04 二进制在 22.04 容器里** —— 已本地跑通（版本字符串能打出来），但完整交易 API 未验证；bootstrap 结束前不清楚。若不兼容，可切 Ubuntu 20.04 base。
- ⚠️ **首次 bootstrap 期间 6080 端口暴露公网** —— 靠 VNC 密码保护，密码是 20 位随机。若担心可以在 `.env` 里 `VNC_BIND=127.0.0.1:6080`，改成只本机可达，通过 SSH 端口转发访问。
- ⚠️ **463 MB 的初次下载** —— 网速慢时容器首启会卡在 Stage 1 好几分钟。日志里有进度。

## 11. 明确不做的事（YAGNI）

- ❌ 不做自动升级检查（用户改 `.env` 才触发）
- ❌ 不做多账号并存（一个 OpenD 实例登一个富途主账号，多账号只在管理后台配 `acc_id` 区分子账户，已支持）
- ❌ 不做 OpenD 集群/HA
- ❌ 不做 Flask 反代 VNC（Section 讨论过，暂缓）
- ❌ 不做 secrets manager 集成，`.env` 够用
