# Fund Lens — 基金盘中估值实时看板

> 基于基金前十大重仓股，实时估算预设基金的盘中涨跌与估值净值。手机端优先。

---

## 1. 项目概述

一个个人用的轻量级 Web 应用：打开网页后，自动、实时地刷新一组**预设基金**的盘中估值。
估值不是直接抄来的，而是**用基金披露的前十大重仓股 + 这些股票的实时涨跌幅，自己加权算出来的**，
同时拉取天天基金的官方盘中估值做对照校验。

- **使用场景**：盘中（A股交易时段）打开手机看自己关注的几只基金大概涨跌多少。
- **核心特征**：手机端优先、实时刷新、轻量、部署简单（docker compose 一键起）。

### 1.1 目标（In Scope）

- 维护一个**基金目录池**（配置/数据库），用户登录后从池中**自选关注的基金**。
- 打开页面即看到自己关注的基金的实时估值卡片。
- 每只基金展示：估算涨跌幅、估算净值、昨日净值、官方估值对照、重仓股明细。
- 盘中按固定间隔自动刷新（SSE 推送），无需手动刷新页面。
- **用户机制**：登录后查看/管理自己的关注列表；**注册不对外**，用户由管理员从数据库层面创建（环境变量种子 + 管理命令，明文进、内部 hash，管理员无需自己算 hash）。
- A股为主；港股/美股做成**可选开关**，默认关闭，架构上预留。
- docker compose 一键部署。

### 1.2 非目标（Out of Scope，至少 v1 不做）

- 不做**公开注册**（用户仅由管理员创建）、不做找回密码/邮箱验证/第三方登录。
- 不做角色权限体系（仅"普通用户"，外加一个能跑管理命令的管理员）。
- 不做交易、下单、持仓盈亏跟踪。
- 不做历史 K 线、净值走势图（v1 只看当下；可作为后续扩展）。
- 不追求与基金公司实际净值完全一致（前十大重仓只覆盖约 50–70% 净值，天然有误差，见 §4.3）。
- 不做实时全持仓（季报只公布前十大，全持仓拿不到）。

---

## 2. 数据源

全部使用**免费公开接口**（东方财富 / 天天基金系），AKShare 仅用于"持仓"这类低频数据。

### 2.1 基金前十大重仓股（低频，每日 1 次）

- **方式 A（推荐，省事）**：AKShare
  - `ak.fund_portfolio_hold_em(symbol="000001", date="2024")` → 返回前十大重仓股：股票代码、名称、占净值比例、持股数、持仓市值。
  - 取最新报告期那一批。
- **方式 B（备用，AKShare 抽风时）**：直连东财
  - `https://fundf10.eastmoney.com/FundArchivesDatas.aspx?type=jjcc&code={fundcode}&topline=10`
  - 返回 JSONP，含报告期、个股代码/名称/占净值比例。

> 持仓季度才更新一次，所以**每天启动时拉一次、缓存到本地**即可，不进实时热路径。

### 2.2 基金基本信息（基金名称、昨日单位净值）

- 天天基金估值接口本身就带：见 §2.4 的 `name` 和 `dwjz`。
- 备用：`https://fund.eastmoney.com/pingzhongdata/{fundcode}.js`（含名称、净值历史、股票仓位等）。

### 2.3 A股实时行情（高频，盘中轮询热路径）★核心

- **批量接口（一次拿全部去重后的股票）**：
  ```
  https://push2.eastmoney.com/api/qt/ulist.np/get?secids={secid列表}&fields=f2,f3,f12,f13,f14
  ```
  - `secids`：逗号分隔，格式 `{market}.{code}`。market 前缀：**沪市/沪指=1，深市=0**。
    - 例：贵州茅台 `1.600519`，平安银行 `0.000001`。
  - 字段：`f2`=最新价(注意是 ×100 的整数，需除以 100)、`f3`=涨跌幅(×100)、`f12`=代码、`f13`=市场、`f14`=名称。
  - **优势**：所有基金去重后的全部重仓股（通常 100–200 只）一次请求搞定，省流量、低延迟。
  - **限流回退**：`push2.eastmoney.com` 偶发限流返空时，自动回退 `push2delay.eastmoney.com`（约 3 分钟延迟，对"估算盘中涨跌"无实质影响，NAV 本就每日只公布一次）。
- 直接用 `httpx` 异步调用即可，**不走 AKShare 的行情函数**（那些偏慢、不适合轮询）。

### 2.4 官方盘中估值（对照校验用）

- `https://fundgz.1234567.com.cn/js/{fundcode}.js`
- 返回 JSONP：`jsonpgz({"fundcode","name","jzrq"(净值日期),"dwjz"(昨日单位净值),"gsz"(估算净值),"gszzl"(估算涨跌幅%),"gztime"})`
- 用途：① 拿昨日净值 `dwjz`；② 把我们自算的涨跌幅和官方 `gszzl` 并排显示，方便判断偏差。

### 2.5 接口注意事项

- 这些都是非官方公开接口，需带常规 `User-Agent`、设置超时与重试。
- 失败要降级（用上一次成功值，并在前端标记"数据延迟/失败"），不能让单点失败拖垮整页。
- 控制频率，避免被限流（批量接口已大幅降低请求数）。

---

## 3. 估值计算

### 3.1 输入

每只基金 `f`：
- 前十大重仓股集合 `H_f`，每只股票 `i` 有占净值权重 `w_i`（百分比，如 8.5 表示占净值 8.5%）。
- 每只股票当日实时涨跌幅 `c_i`（百分比）。
- 基金昨日单位净值 `dwjz_f`。

### 3.2 公式

提供两种口径，配置可选，默认 **normalized**：

- **raw（保守，低估）**：
  ```
  est_pct = Σ(w_i/100 × c_i)
  ```
  未覆盖的净值部分默认按 0 计，故偏小。

- **normalized（按已知重仓等比例放大，更接近体感）**：
  ```
  est_pct = Σ(w_i × c_i) / Σ(w_i)
  ```
  假设剩余仓位涨跌与前十大整体一致。一般更贴近官方估值。

估算净值：
```
est_nav = dwjz_f × (1 + est_pct/100)
```

### 3.3 误差说明（务必前端注明）

- 前十大重仓股通常只占基金净值 **50–70%**，其余仓位、债券/现金、打新、对冲等都看不到。
- 持仓是**季报滞后数据**，实际持仓早已变动。
- 因此本估值是**参考值**，与基金公司次日公布的真实净值会有偏差；官方 `gszzl` 也只是估算。
- v1 不追求精确，重点是"盘中大致方向 + 与官方估值对照"。

---

## 4. 系统架构

```
┌──────────────┐  登录(cookie session)        ┌───────────────────────────┐
│  浏览器(手机)  │ ◀───────────────────────────│  FastAPI 后端 (单容器)      │
│  登录页+看板   │   SSE / snapshot (按用户过滤) │                           │
│  EventSource  │ ───────────────────────────▶│  - 后台刷新协程(asyncio)    │
└──────────────┘   关注列表管理                 │  - 内存快照(全目录池)       │
                                             │  - SQLite(用户/目录/关注)   │
                                             └──────────┬────────────────┘
                                                        │  httpx 异步请求
                                          ┌─────────────┼──────────────┐
                                          ▼             ▼              ▼
                                    AKShare(持仓)  push2(行情)   fundgz(官方估值)
```

- **引擎对"全部用户关注的并集 + 配置种子池"统一计算**估值快照（数据共享、只算一次），每个用户的 `/api/snapshot` 与 `/api/stream` **按其关注列表过滤**后下发。用户可**自由添加任意基金代码**（校验有效后纳入计算），不再受限于预设池。
- **SQLite**：存用户、基金目录、用户-关注关系（轻量、随容器卷持久化）。持仓/行情快照仍放内存；持仓另存本地 JSON 做冷启动缓存。
- **单进程单容器**：FastAPI + 静态前端由同一个服务托管。
- **实时机制**：后台一个 asyncio 任务按间隔拉数据→算估值→更新内存快照→通过 SSE 把各用户过滤后的视图广播给已登录的浏览器。

---

## 5. 后端设计（Python / FastAPI）

### 5.1 技术选型

- `FastAPI` + `uvicorn`（ASGI，原生支持 SSE / 长连接）。
- `httpx`（异步 HTTP，批量行情 & 官方估值）。
- `akshare`（仅持仓）。
- `pydantic`（配置与数据模型校验）。
- `PyYAML`（读配置）。

### 5.2 模块划分

```
app/
  main.py            # FastAPI 入口、路由、SSE、静态文件挂载
  config.py          # 加载 funds.yaml + 环境变量
  models.py          # pydantic 数据模型
  sources/
    holdings.py      # AKShare/东财 拉前十大重仓股 + 本地缓存
    quotes.py        # push2 批量行情
    official.py      # fundgz 官方估值 + 昨日净值
  engine.py          # 估值计算 + 后台刷新调度 + 快照管理 + SSE 广播
  market.py          # 交易时段判断、股票代码→secid 映射、多市场开关
static/              # 前端构建产物 / 静态文件
funds.yaml           # 预设基金配置
```

### 5.3 数据模型（pydantic）

```python
class Holding:
    code: str          # 股票代码 600519
    name: str          # 贵州茅台
    market: str        # "A" | "HK" | "US"  (v1 主要 A)
    weight: float      # 占净值比例 %
    change_pct: float | None   # 实时涨跌幅 %
    price: float | None        # 实时价

class FundSnapshot:
    code: str
    name: str
    prev_nav: float            # 昨日单位净值 dwjz
    est_pct: float | None      # 我们算的估算涨跌幅 %
    est_nav: float | None      # 估算净值
    official_pct: float | None # 官方 gszzl
    official_nav: float | None # 官方 gsz
    holdings: list[Holding]
    report_date: str           # 持仓报告期
    coverage: float            # 前十大合计占净值比例（Σw_i）
    updated_at: datetime
    status: str                # "ok" | "stale" | "error"

class Snapshot:               # 整体快照
    funds: list[FundSnapshot]
    market_open: bool
    server_time: datetime
```

### 5.4 后台刷新调度

- 启动时：加载配置 → 拉一次持仓（命中本地缓存则跳过）→ 拉昨日净值。
- 持仓刷新：每日一次（或进程启动时），写本地 JSON 缓存。
- 行情刷新（热路径）：
  - **盘中**（A股 9:30–11:30、13:00–15:00，工作日）：每 `REFRESH_INTERVAL` 秒（默认 10s，可配）。
  - **非盘中**：降到很低频（如每 5 分钟，仅同步官方收盘估值）或暂停，前端显示"已收盘"。
- 每轮：去重所有股票 → 一次批量行情请求 → 逐基金算估值 → 拉官方估值 → 更新内存快照 → SSE 广播。

### 5.5 API

| 方法 | 路径 | 鉴权 | 说明 |
|---|---|---|---|
| POST | `/api/login` | 否 | 表单 `username/password`，校验通过写入 session cookie |
| POST | `/api/logout` | 是 | 清除 session |
| GET | `/api/me` | 是 | 返回当前用户名（前端判断登录态） |
| GET | `/api/catalog` | 是 | 基金目录池（含名称、市场），供用户勾选关注 |
| GET | `/api/watchlist` | 是 | 当前用户关注的基金代码列表 |
| PUT | `/api/watchlist` | 是 | 整体更新当前用户的关注列表（body: `{codes:[...]}`，仅限目录内代码） |
| POST | `/api/watchlist/add` | 是 | **自由添加任意基金**：校验 6 位代码→入目录→加关注→即时计算（body: `{code}`） |
| GET | `/api/snapshot` | 是 | 当前用户**关注基金**的完整快照（首屏 / SSE 断线兜底） |
| GET | `/api/stream` | 是 | **SSE** 流，推送该用户关注基金的最新快照（`event: snapshot`） |
| GET | `/healthz` | 否 | 健康检查（docker / 探活用） |
| GET | `/` 及静态 | 否 | 前端页面（未登录跳登录视图） |

> 鉴权用 starlette `SessionMiddleware`（签名 cookie，无需额外服务）；受保护路由用一个 FastAPI 依赖 `require_user` 读取 session，未登录返回 401。

- SSE 心跳：定期发送注释行 `:keepalive` 防止中间代理断连。
- 断线重连：浏览器 `EventSource` 自带重连；并以 `/api/snapshot` 轮询兜底。

### 5.6 容错与降级

- 单只股票/单只基金失败：保留上次值，该项 `status=stale`，不影响其它。
- 整体行情请求失败：整页 `status=error` + 上次快照，前端置灰提示。
- 所有外部请求设超时（连接 3s / 读 5s）+ 有限重试 + 退避。

### 5.7 数据库与用户机制（SQLite）

- 驱动：`aiosqlite`（异步、零服务、随卷持久化），手写少量 SQL，不引入重型 ORM。
- 表结构：
  ```sql
  CREATE TABLE users (
    id INTEGER PRIMARY KEY,
    username TEXT UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,      -- pbkdf2_hmac(sha256)，格式 algo$iter$salt$hash
    is_active INTEGER DEFAULT 1,
    created_at TEXT
  );
  CREATE TABLE funds (                 -- 基金目录池（管理员维护，启动时从 funds.yaml 种子）
    code TEXT PRIMARY KEY,
    name TEXT,
    market TEXT DEFAULT 'A'
  );
  CREATE TABLE user_funds (            -- 用户关注关系
    user_id INTEGER NOT NULL,
    fund_code TEXT NOT NULL,
    sort_order INTEGER DEFAULT 0,
    PRIMARY KEY (user_id, fund_code)
  );
  ```
- **密码哈希**：标准库 `hashlib.pbkdf2_hmac`（无第三方依赖），加随机 salt；校验用 `hmac.compare_digest` 防时序攻击。管理员/用户全程只接触明文，hash 由程序内部完成。
- **创建用户的两种方式（均明文进、内部 hash）**：
  1. **环境变量种子**：启动时若 `users` 为空且设置了 `ADMIN_USER`/`ADMIN_PASSWORD`，自动建首个用户。
  2. **管理命令** `python -m app.admin`：
     - `add <username> <password>` 新增用户
     - `passwd <username> <newpassword>` 改密
     - `list` 列出用户
     - `delete <username>` 删除
     - 容器内执行：`docker compose exec fund-lens python -m app.admin add alice s3cret`
- **基金目录池**：启动时把 `funds.yaml` 的 `funds` 列表 upsert 进 `funds` 表（名称从首次快照回填）。用户关注列表为空时，前端引导其从目录勾选。
- **会话**：登录成功后 `request.session["uid"]=user_id`；`SECRET_KEY` 由环境变量提供（用于 cookie 签名）。

---

## 6. 前端设计（手机端优先，轻量）

### 6.1 技术选型

- **零构建、纯静态**：原生 `HTML + CSS + 原生 JS（ES Module）`，不引入 React/Vue 等重框架，契合"轻量级"。
  - 如需一点响应式渲染体验，可选 `Alpine.js`（~15KB）或 `Preact + htm`（~5KB，CDN 引入，无需打包）。默认先用原生 JS，够用再升级。
- 数据走 **SSE（EventSource）**，单向实时推送，移动端友好、比 WebSocket 简单。
- 移动优先：`viewport` 适配、`rem`/`clamp()` 流式排版、深色模式（盘中看盘护眼）。

### 6.2 页面结构（单页）

- **登录视图**：未登录时显示用户名/密码表单（POST `/api/login`）；登录后切到看板视图。
- **关注管理**：从目录池（`/api/catalog`）勾选/取消关注，保存到 `/api/watchlist`；关注为空时引导去添加。
- **顶部状态栏**：市场状态（交易中/已收盘）、最后更新时间、连接状态（实时/重连中）、登出按钮。
- **基金卡片列表**（每只一张卡，纵向滚动）：
  - 第一行：基金名称 + 代码。
  - 主数据：**估算涨跌幅**（大号，红涨绿跌，A股习惯），右侧估算净值。
  - 副数据：昨日净值、官方估值对照（`官方 +1.23%`）、前十大覆盖度 `coverage%`、报告期。
  - 可展开：前十大重仓股明细（名称、权重、实时涨跌幅，红绿着色）。
- **底部**：免责声明（"估值仅供参考，非真实净值"）。

### 6.3 交互

- 打开即连 SSE，自动滚动刷新数字（数字变化时高亮闪一下，体现"实时流畅"）。
- 下拉刷新 / 顶部手动刷新按钮（调 `/api/snapshot`）。
- 卡片点按展开/收起重仓明细。
- 涨跌颜色遵循 A 股：**红涨绿跌**（可配置切换为国际配色）。

### 6.4 性能/流畅

- 只更新变化的 DOM 节点（diff 数字），避免整列表重渲染。
- 数字滚动/闪动用 CSS transition，轻量不卡。
- 资源全部本地托管或 CDN，首屏 < 100KB。

---

## 7. 配置

### 7.1 基金目录池 `funds.yaml`

```yaml
refresh_interval_seconds: 10      # 盘中刷新间隔
valuation_method: normalized       # normalized | raw
color_scheme: cn                   # cn(红涨绿跌) | intl
markets:
  a_share: true                    # A股，默认开
  hk: false                        # 港股，预留可选
  us: false                        # 美股，预留可选
funds:                             # 目录池：用户从这里勾选关注
  - "000001"   # 华夏成长混合
  - "110011"   # 易方达优质精选
  - "005827"   # 易方达蓝筹精选
  # ... 可供选择的基金代码列在这里
```

- 启动时同步进 `funds` 表（目录池）。用户的关注列表存在数据库，与此文件解耦。

### 7.2 环境变量

- `SECRET_KEY`：session cookie 签名密钥（**生产务必设置**）。
- `ADMIN_USER` / `ADMIN_PASSWORD`：首次启动种子用户（库为空时生效）。
- `DB_PATH`：SQLite 路径，默认 `/app/data/fundlens.db`。
- `REFRESH_INTERVAL`、`VALUATION_METHOD`、`HTTP_PROXY`（可选代理）、`TZ=Asia/Shanghai`。

---

## 8. 交易时段处理

- 由 `market.py` 统一判断：
  - A股工作日 09:30–11:30、13:00–15:00 为"盘中"，高频刷新。
  - 集合竞价、午休、收盘后切换状态，前端展示对应文案。
  - 简化处理节假日：v1 用"周末 + 可配置节假日列表"判断；后续可接交易日历接口。
- 多市场开启时，各市场独立时段（港股 09:30–16:00、美股需处理时区与夏令时），见 §9。

---

## 9. 多市场扩展（可选，默认关闭）

- 数据模型 `Holding.market` 已预留；`market.py` 负责"股票代码 → secid 前缀"映射：
  - A股：沪 `1.`、深 `0.`（按代码段判断）。
  - 港股：`116.`；美股：`105./106./107.`。
- 开启港股/美股需补充：各自交易时段、**汇率换算**（净值以人民币计，美股/港股需乘汇率）、夜间行情。
- v1 仅把开关和字段留好，**不实现**汇率与跨时区逻辑，避免 A股主线被拖慢。

---

## 10. 部署（Docker Compose）

### 10.1 目录

```
fund-lens/
  app/                  # 后端代码（FastAPI + 引擎 + SQLite + 数据源）
  static/               # 前端静态文件（index.html / app.js / style.css）
  deploy/               # systemd 单元 + 安装脚本
  funds.yaml            # 基金目录池（挂载进容器）
  .env / .env.example   # 配置与密钥（.env 不入库/不进镜像）
  requirements.txt
  Dockerfile
  docker-compose.yml
  SPEC.md
```

### 10.2 Dockerfile（要点）

- 基于 `python:3.12-slim`，装依赖，拷代码，`uvicorn app.main:app --host 0.0.0.0 --port 8000`。
- AKShare 依赖较多（pandas 等），镜像偏大属正常。

### 10.3 docker-compose.yml（要点）

```yaml
services:
  fund-lens:
    build: .
    ports:
      - "8000:8000"
    environment:
      - TZ=Asia/Shanghai
      - REFRESH_INTERVAL=10
      - SECRET_KEY=change-me-to-a-long-random-string
      - ADMIN_USER=admin                          # 首次启动种子用户
      - ADMIN_PASSWORD=change-me
      - DB_PATH=/app/data/fundlens.db
    volumes:
      - ./funds.yaml:/app/funds.yaml:ro          # 改目录池不重新构建
      - ./data:/app/data                          # SQLite + 持仓缓存持久化
    restart: unless-stopped
    healthcheck:
      test: ["CMD", "wget", "-qO-", "http://localhost:8000/healthz"]
      interval: 30s
      timeout: 5s
      retries: 3
```

- 配置全部放 `.env`（不进版本库/镜像），compose 用 `env_file: .env` 注入；`.env.example` 为模板。
- 启动：`cp .env.example .env` → 改 `SECRET_KEY`/`ADMIN_PASSWORD` → `docker compose up -d --build`，访问 `http://<host>:8000`。

### 10.4 systemd 部署（非 Docker，单进程）

应用本身就是一个 uvicorn 进程，也可直接用 systemd 托管：

- 单元文件 `deploy/fund-lens.service`：`EnvironmentFile=.env`，`ExecStart` 跑 venv 里的 uvicorn，`Restart=always`，专用账号运行。
- 一键脚本 `deploy/install-systemd.sh`（root 执行）：建账号 → 建 venv 装依赖 → 生成 `.env`（自动随机 `SECRET_KEY`、把 `DB_PATH` 指向绝对路径）→ 写入并 `enable --now`。
- 数据库仍是同一个 SQLite 文件（`DB_PATH`），管理命令：
  `sudo -u fundlens /opt/fund-lens/.venv/bin/python -m app.admin add <用户名> <密码>`
- 日志：`journalctl -u fund-lens -f`。

> Docker 与 systemd 二选一即可；两者共用同一套代码、`.env` 和 SQLite 数据。

---

## 11. 开发里程碑

1. **M1 数据打通**：拉一只基金的持仓 + 批量行情 + 官方估值，命令行打印自算估值并与官方对照。
2. **M2 后端服务**：FastAPI + 内存快照 + `/api/snapshot` + 后台刷新协程 + 多基金 + 容错降级。
3. **M2b 数据库与用户**：SQLite（用户/目录/关注表）、密码 hash、管理命令、环境变量种子用户。
4. **M2c 鉴权与过滤**：登录/登出/会话、`require_user` 依赖、按用户关注过滤快照与 SSE、关注列表 API。
5. **M3 实时推送**：SSE `/api/stream`，断线重连，交易时段调度。
6. **M4 前端**：登录视图 + 关注管理 + 手机端卡片列表、重仓展开、红绿配色、数字实时刷新。
7. **M5 部署**：Dockerfile + docker-compose，配置挂载，健康检查，联调上线。
8. **M6（可选）**：港股/美股开关落地、净值走势图、节假日日历。

---

## 12. 风险与免责

- 估值基于季度滞后的前十大重仓，仅覆盖部分净值，**为参考值，非基金真实净值**，不构成投资建议。
- 数据来自非官方公开接口，存在变更、限流、失效风险，需做好降级与监控。
- 页面与文档需明确标注免责声明。

---

## 13. 待确认 / 可调整项

1. 盘中刷新间隔默认 10s 是否合适？（更快更"实时"但请求更多，10s 对个人足够。）
2. 估值口径默认 `normalized`，是否同意？（还是想同时显示 raw + normalized 两个值？）
3. 前端先用**原生 JS**起步，够用即可；如果你想要更顺滑的组件化体验，可换 Alpine/Preact。是否同意原生起步？
4. 节假日判断 v1 用"周末 + 手动节假日列表"的简化方案，是否接受？
5. 预设基金清单：你先给我一组基金代码，我放进 `funds.yaml` 作为默认。
