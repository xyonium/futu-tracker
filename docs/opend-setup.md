# OpenD Docker 部署与首次登录手册

本项目 Docker Compose 一次性拉起两个服务：

| 服务      | 端口     | 说明                                                    |
|-----------|----------|---------------------------------------------------------|
| `tracker` | 5000     | Flask Web 站点，暴露公网                                |
| `opend`   | 11111    | Futu OpenD 网关，**仅 Docker 内网可达**                 |
| `opend`   | 6080     | noVNC 首次登录网页，**仅 bootstrap 阶段监听**           |

## 首次部署（约 5–10 分钟）

### 1. 准备 `.env`

```bash
cp .env.example .env
```

至少设置：

```ini
FUTU_LOGIN_ACCOUNT=<你的富途/moomoo ID、手机号或邮箱>
FUTU_LOGIN_PWD_MD5=<密码的 32 位 MD5>    # 生成: echo -n '你的密码' | md5sum
FLASK_SECRET_KEY=$(openssl rand -hex 32)
```

`FUTU_LOGIN_PWD_MD5` 是**必须的** —— CLI 进程不共享 VNC GUI 的内存登录态，
每次 CLI 启动都需要用密码 MD5 重新认证。

### 2. 启动

```bash
docker compose up -d
docker compose logs -f opend
```

日志会走这几个阶段：

```
[opend] installed version: none, target: 10.8.6808 — downloading
[opend] downloading OpenD version 10.8.6808...           ← 约 460 MB, 首次较慢
[opend] OpenD 10.8.6808 installed to /data
[opend] rendered config to /config/FutuOpenD.xml
[opend] ════════════════════════════════════════════════════════════════
[opend] NO VALID FINGERPRINT — launching VNC bootstrap
[opend]   Open a browser to:  http://<this-server>:6080/vnc.html
[opend]   VNC password:       Xa9pQ7mK2wLbY4nJ8vRt          ← 复制这个
[opend] ════════════════════════════════════════════════════════════════
```

### 3. 网页 VNC 完成 OpenD 登录

1. 浏览器打开 `http://<你的服务器 IP>:6080/vnc.html`
2. 输入日志里的 VNC 密码，点 Connect
3. 看到 OpenD GUI 桌面
4. 输入富途账号 + 密码 → 富途会给你手机发短信/邮箱验证码
5. 输入验证码，勾选"信任此设备"，登录
6. 看到 OpenD 主界面显示"已连接"、账户列表出现即成功

回到终端，日志会自动继续：

```
[opend] fingerprint acquired — recording paths for future startups
[opend] checkpoint files:
[opend]   /data/.com.futunn.FutuOpenD/F3CNN/Device.dat
[opend] shutting down VNC layer to reclaim memory...
[opend] starting headless CLI FutuOpenD on port 11111
```

**此时 6080 端口不再响应，OpenD 只在 Docker 内网监听 11111。**

### 4. CLI 首次验证码（只需一次）

CLI 进程首次连接富途服务器时，**同样需要一次手机验证码**来建立 CLI 进程的
设备信任。这是 CLI 独立于 GUI 的安全要求，只在**这第一次**发生。

日志中会看到：

```
>>> 命令提示: input_phone_verify_code -code=123456
>>> 正在请求手机验证码
>>> 请求手机验证码成功
```

收到短信后，用容器内置的 `nc` 通过 telnet 控制台发送验证码：

```bash
docker exec futu-opend bash -c \
  'printf "input_phone_verify_code -code=你的6位验证码\r\n" | nc -w 5 localhost 22222'
```

✅ 验证通过后 OpenD 会绑定设备指纹，**以后所有重启都不再需要验证码**。

### 5. 配置 tracker 连 OpenD

打开 `http://<你的服务器 IP>:5000`，用 `admin` / `admin123` 登录 →
管理后台 → OpenD 配置：

```
futu_host = opend         ← 就是这个服务名，不是 127.0.0.1
futu_port = 11111
```

保存 → 立即同步数据 → 看到账户余额即成功。

⚠️ **务必立刻改掉 admin 密码**（管理后台 → 用户管理）。

## 日常运维

### 查看状态

```bash
docker compose ps
docker compose logs -f tracker     # Flask 日志
docker compose logs -f opend       # OpenD 日志
```

### 升级 OpenD 版本

```bash
# 编辑 .env
OPEND_VERSION=10.9.xxxx

docker compose up -d
# 容器检测到版本变化 → 下载新版 → 强制重新走 VNC bootstrap（因为指纹通常也会失效）
docker compose logs -f opend
# 按"首次部署"流程 3 重新在浏览器完成登录
```

### 强制重新登录（例如指纹坏了、想换账号）

  ```bash
docker compose exec opend rm -f /data/.fingerprint_paths
docker compose exec opend sh -c 'find /data/.com.futunn.FutuOpenD -name "Device.dat" -delete'
docker compose restart opend
docker compose logs -f opend    # 会再次触发 VNC bootstrap
```

如果连数据都想清空：

```bash
docker compose down
docker volume rm futu-tracker_opend-data futu-tracker_opend-config
docker compose up -d
```

### 忘记 VNC 密码

```bash
docker compose exec opend cat /data/.vnc_password
```

或者在 `.env` 里设 `VNC_PASSWORD=<你想要的>` 后 `docker compose up -d`
（下次 bootstrap 生效）。

## 安全建议

- **暴露公网时**：改 `.env` 里 `VNC_BIND=127.0.0.1`，然后从本地
  `ssh -L 6080:localhost:6080 user@server` 转发过来访问 VNC。
- **不建议**把 11111 暴露到公网（OpenD 自己没有强鉴权）。当前 compose
  设的是 `expose`（Docker 内网可见）而不是 `ports`，保持这个默认。
- **VNC 密码**是每卷随机 20 位。要更强就在 `.env` 手动设 `VNC_PASSWORD`。
- Bootstrap 结束后 `pkill websockify` 会立刻关掉 6080 的监听进程，从外面
  访问会连不上（尽管 Docker 层的端口映射还在）。

## 排错

**"download failed" / 卡在下载**
富途 CDN 有时慢。手动测：
```bash
curl -sIL "https://www.futunn.com/download/fetch-lasted-link?name=opend-ubuntu"
```
可以在 `.env` 里显式设 `OPEND_DOWNLOAD_URL=<直接的 tar.gz URL>` 绕开 302 探测。

**VNC 打开是黑屏**
Xvfb / fluxbox 启动慢，等 10 秒再刷新。或看：
```bash
docker compose exec opend cat /tmp/x11vnc.log
```

**Login 反复失败 / 验证码收不到**
富途安全策略可能限制新 IP 段。去富途 App 里检查"设备管理"，把 OpenD 的
登录尝试标为受信；或者先在本地跑一次登录成功，再上服务器。

**"fingerprint acquired" 但 CLI 起不来**
版本可能被富途标记为过期。查 CLI 日志：
```bash
docker compose logs opend | tail -50
```
升级 `OPEND_VERSION`。

**nc 发验证码后没反应**
确认 `\r\n` 换行符是否丢了（telnet 协议需要 CRLF）：
```bash
docker exec futu-opend bash -c \
  'printf "input_phone_verify_code -code=123456\r\n" | nc -w 5 localhost 22222'
```
注意是 `printf` 不是 `echo`，`\r\n` 必须带反斜杠。

**tracker 侧 "get_acc_list failed"**
检查 tracker 是否能连到 opend：
```bash
docker compose exec tracker nc -zv opend 11111
```
若不通，检查 `futu_host` 配置项在管理后台里的值是不是 `opend`（而不是 `127.0.0.1`）。

## 内存参考

| 状态                 | tracker | opend        | 合计       |
|----------------------|---------|--------------|------------|
| 常驻                 | ~80 MB  | ~150 MB      | ~230 MB    |
| Bootstrap 触发（首次或重登） | ~80 MB  | ~500–700 MB  | ~600–800 MB |
| 数据同步中           | ~100 MB | ~200 MB      | ~300 MB    |

镜像磁盘：`opend` ~250 MB + `tracker` ~200 MB。
Volume 磁盘：`opend-data` ~300 MB（CLI + AppImage + 库）。
