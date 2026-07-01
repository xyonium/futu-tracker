# Futu Portfolio Tracker

多账户投资组合追踪器 - 支持 Futu HK / Moomoo US / Moomoo AU 三个账户，自动汇率折算，净值计算与可视化。

## 📁 项目结构

```
futu_portfolio/
├── app.py                 # Flask 主应用 (路由/认证/API)
├── futu_client.py         # Futu OpenD API 客户端 (数据获取/汇率/NAV计算)
├── scheduler.py           # 定时任务 (每日自动同步)
├── requirements.txt       # Python 依赖
├── Dockerfile             # Docker 构建文件
├── docker-compose.yml     # Docker Compose 编排
├── .gitignore
├── templates/
│   ├── login.html         # 登录页
│   ├── dashboard.html     # 仪表盘主页 (净值曲线+账户明细)
│   └── admin.html         # 管理后台 (配置/用户管理)
├── static/
│   ├── css/style.css      # 暗色主题样式
│   └── js/
│       ├── dashboard.js   # 仪表盘交互 (Chart.js 图表)
│       └── admin.js       # 管理后台交互
└── data/                  # 运行时生成 (已 gitignore)
    ├── portfolio.db       # SQLite 数据库
    ├── .encryption_key    # API Key 加密密钥
    └── scheduler.log      # 定时任务日志
```

## 🚀 快速开始

### 方式一：直接运行

```bash
cd futu_portfolio
pip install -r requirements.txt
python app.py
```

访问 http://localhost:5000，默认管理员账号：`admin` / `admin123`

### 方式二：Docker

```bash
cd futu_portfolio
docker-compose up -d
```

## ⚙️ 配置步骤

1. **登录** → 使用 admin/admin123
2. **进入管理后台** → 点击导航栏"管理后台"
3. **配置 OpenD 连接** → 填写 OpenD 地址和端口 (需先启动 Futu OpenD)
4. **配置账户** → 设置三个账户的 市场/Account ID/交易环境
5. **设置初始参数** → 填写初始日期和初始金额 (HKD)
6. **首次同步** → 点击"立即同步数据"
7. **修改密码** → 务必修改默认管理员密码！

## 📊 功能特性

| 功能 | 说明 |
|------|------|
| 多账户汇总 | Futu HK (HKD) + Moomoo US (USD) + Moomoo AU (AUD) |
| 汇率折算 | 自动获取当日中间价，折算为 HKD |
| 净值计算 | 总资产 ÷ 初始金额 = 净值，显示盈亏比例和金额 |
| 净值曲线 | Chart.js 绘制，涨绿跌红 |
| 时间切换 | 近3月/6月/12月/今年以来/成立以来 |
| 登录认证 | 用户名密码，Session 管理 |
| 管理后台 | API配置/账户管理/用户管理/修改密码 |
| API Key安全 | Fernet 加密存储，不暴露到前端 |
| 定时同步 | scheduler.py 配合 cron 每日自动获取 |

## 🔒 安全措施

- API Key 使用 Fernet 对称加密存储在 SQLite，密钥文件权限 600
- 密码使用 Werkzeug 的 pbkdf2 哈希
- Session HttpOnly + SameSite=Lax
- 管理接口有 `@admin_required` 装饰器
- `.gitignore` 排除 data/ 目录
- 前端永远不接触 API Key

## ⏰ 定时任务设置

```bash
# crontab -e
# 每个工作日 HKT 18:00 自动同步
0 18 * * 1-5 cd /path/to/futu_portfolio && python scheduler.py
```

## 📝 前置要求

- **Futu OpenD** 必须在同一网络运行 (本地或远程)
- 三个账户需在 OpenD 中登录
- Python 3.9+
