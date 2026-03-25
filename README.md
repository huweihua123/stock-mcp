# Stock MCP

<div align="center">

**[English](#english) | 中文**

开源金融数据 MCP / HTTP 服务，面向 AI Agent、量化脚本和普通后端集成。

</div>

## 中文

### 这是什么

`stock-mcp` 是一个开源金融数据服务，提供两套对外接口：

- `MCP`：给 Claude Desktop、Cursor、DeerFlow、各类 Agent 使用
- `HTTP API`：给普通后端、脚本和服务直接调用

它的定位不是“给投资建议”，而是提供：

- 稳定的数据源接入
- 标准化的金融工具能力
- 适合 Agent 使用的 MCP tool / artifact 输出
- 适合代码执行工作流的 CSV / JSON 数据导出

### 一键启动

如果你只是想先把服务跑起来，最短路径就是：

```bash
git clone https://github.com/yourusername/stock-mcp.git
cd stock-mcp
cp .env.example .env
docker compose up -d --build
```

启动后默认可访问：

- `http://127.0.0.1:9898/health`
- `http://127.0.0.1:9898/docs`
- `http://127.0.0.1:9898/mcp`

默认 Docker 栈会一起启动：

- `stock-mcp`
- `redis`
- `postgres`

默认端口：

- `stock-mcp`: `127.0.0.1:9898`
- `postgres`: `127.0.0.1:15432`

说明：

- `15432` 是为了避免占用你本机已有的 `5432`
- 容器内部仍然使用 `postgres:5432`
- 如果你想修改宿主机映射端口，改 `DOCKER_POSTGRES_PORT` 即可

### 核心功能

#### 1. 市场数据

- A 股、美股、ETF、指数、加密资产行情
- 日线 / 分时 / K 线数据
- 多市场 ticker 解析与标准化
- 多数据源回退

#### 2. 技术分析

- RSI、MACD、均线、布林带等常见指标
- K 线形态识别
- 支撑 / 压力位分析
- 趋势与动量辅助分析

#### 3. 基本面与研究

- 估值与基础财务指标
- 美股宏观和行业研究工具
- 基于 Tavily 的统一新闻检索
- 资金流与筹码分布

#### 4. 公告与文档

- SEC / EDGAR filings
- A 股公告处理
- 文档 chunk / markdown / 结构化提取

#### 5. Agent / Code Workflow 友好能力

- MCP tools 统一注册
- MCP artifact 减载，避免把大 blob 直接塞进模型上下文
- `code-export` HTTP 接口，可直接导出 CSV / JSON 给代码执行型 agent

### 支持的主要数据源

国内源：

- `Tushare`
- `Akshare`
- `Baostock`

国外源：

- `Yahoo / yfinance`
- `Finnhub`
- `Alpha Vantage`
- `Twelve Data`
- `FRED`
- `CCXT`
- `Crypto`
- `EDGAR`

### HTTP API 能做什么

当前 HTTP 路由主要分为这些组：

- `/api/v1/market/*`
  - 行情、K 线、技术指标、市场数据查询
- `/api/v1/fundamental/*`
  - 基本面与估值
- `/api/v1/money-flow/*`
  - 资金流分析
- `/api/v1/news/*`
  - 统一新闻检索与个股新闻搜索
- `/api/v1/filings/*`
  - SEC / A 股公告与文档处理
- `/api/v1/code-export/*`
  - 面向代码执行工作流的数据导出
- `/health`
  - 健康检查
- `/docs` / `/redoc`
  - OpenAPI 文档

也就是说，这个项目不是只有 MCP。

如果你不打算接 MCP 客户端，完全可以只把它当普通 HTTP 服务来用。

### MCP 能做什么

MCP 侧会把这些能力暴露成 tools，主要对应：

- 行情和资产查询
- 技术分析
- 基本面与行业研究
- 新闻与事件
  - 当前统一走 Tavily 检索，不再保留 adapter 原生新闻分支
- 资金流与筹码
- 公告与文档处理

MCP 入口：

- `POST /mcp`

适用场景：

- Claude Desktop
- Cursor
- DeerFlow
- 其他支持 MCP 的 Agent 平台

### 大体架构

```text
                +-----------------------------+
                |    MCP Clients / HTTP Apps  |
                | Claude / Cursor / Backends  |
                +--------------+--------------+
                               |
                               v
                    +-----------------------+
                    |   Thin Transports     |
                    |  HTTP / MCP / Docs    |
                    +-----------+-----------+
                                |
                                v
                    +-----------------------+
                    |        Runtime        |
                    | auth / proxy / health |
                    | lifecycle / registry  |
                    +-----------+-----------+
                                |
                +---------------+----------------+
                |                                |
                v                                v
        Capability Plugins                Provider Plugins
      market / filings / news         tushare / yahoo / edgar
      money_flow / code_export        finnhub / fred / akshare
                |                                |
                +---------------+----------------+
                                |
                                v
                           Redis / Postgres
```

职责分层大致是：

- `src/server/runtime/`
  - 稳定骨架：配置、鉴权、代理策略、provider/capability registry、生命周期、健康状态
- `src/server/capabilities/`
  - 业务能力插件：每个能力自带 `plugin / service / http / mcp / schema`
- `src/server/providers/`
  - 数据源插件：每个 provider 声明自己支持的 contract
- `src/server/transports/`
  - 薄入口：HTTP / MCP 只负责协议适配，不承载业务判断
- `src/server/domain/`
  - 保留 provider 路由、符号解析、部分领域服务等底层实现；不再作为新增能力入口

当前重构方向是：

- 不再围绕一个大而全的 `BaseDataAdapter` 扩展
- 不再让 route / tool / service 三处并行增长
- 新能力优先通过 capability plugin 接入
- 新数据源优先通过 provider plugin + contract 接入

### 认证模式

`stock-mcp` 支持三档认证：

#### 1. `none`

- 本地开发 / 开源试用默认模式
- 不做鉴权
- 推荐只监听 `127.0.0.1`

#### 2. `token`

- 适合个人部署或内网服务
- 所有受保护路径统一使用静态 Bearer token

#### 3. `jwt`

- 适合生产和平台集成
- 基于 `JWTVerifier + OIDC / JWKS`
- 不绑定 Keycloak，兼容任意 OIDC/JWT IdP

在 `token` 和 `jwt` 模式下：

- `/health` 匿名开放
- `/mcp`、`/api/v1/*`、`/docs`、`/redoc`、`/openapi.json`、`/` 需要 Bearer token

### 代理规则

当前项目采用“国外源显式代理，国内源直连”的边界：

显式走代理：

- `Yahoo / yfinance`
- `Finnhub`
- `Futures`
- `Alpha Vantage`
- `Twelve Data`
- `FRED`
- `CCXT`
- `Crypto`
- `EDGAR`

保持直连：

- `Tushare`
- `Akshare`
- `Baostock`

Docker 下默认会把：

- `PROXY_HOST`

覆盖成：

- `host.docker.internal`

这样容器里的国外源可以访问宿主机代理。  
如果你的代理不在宿主机上，可以改：

- `DOCKER_PROXY_HOST`

### 本地运行方式

#### 1. uv 本地运行

```bash
uv sync --dev
cp .env.example .env

export STOCK_MCP_AUTH_MODE=none
uv run python -m uvicorn src.server.app:app --host 127.0.0.1 --port 9898
```

#### 2. Docker Compose 运行

```bash
cp .env.example .env
docker compose up -d --build
```

如果你切换过数据库配置或旧卷和当前配置不一致，建议首次执行：

```bash
docker compose down -v
docker compose up -d --build
```

#### 3. stdio MCP

```bash
uv run python -c "import src.server.mcp.server as m; m.create_mcp_server().run(transport='stdio')"
```

### 调用示例

#### HTTP API 示例

```bash
curl -X POST http://127.0.0.1:9898/api/v1/technical/indicators/calculate \
  -H "Content-Type: application/json" \
  -d '{
    "symbol": "SSE:600519",
    "period": "6mo",
    "interval": "1d"
  }'
```

#### MCP 示例

```bash
curl -X POST http://127.0.0.1:9898/mcp \
  -H "Content-Type: application/json" \
  -d '{
    "jsonrpc": "2.0",
    "method": "tools/call",
    "params": {
      "name": "get_kline_data",
      "arguments": {
        "symbol": "SSE:600519"
      }
    },
    "id": "1"
  }'
```

### 常用环境变量

推荐优先关注这些变量：

- `STOCK_MCP_AUTH_MODE=none|token|jwt`
- `STOCK_MCP_STATIC_BEARER_TOKEN`
- `MCP_HOST`, `MCP_PORT`
- `DOCKER_POSTGRES_PORT`, `DOCKER_POSTGRES_USER`, `DOCKER_POSTGRES_PASSWORD`, `DOCKER_POSTGRES_DB`
- `PROXY_ENABLED`, `PROXY_HOST`, `PROXY_PORT`
- `DOCKER_PROXY_HOST`
- `TUSHARE_ENABLED`, `TUSHARE_TOKEN`, `TUSHARE_HTTP_URL`
- `FINNHUB_ENABLED`, `FINNHUB_API_KEY`
- `TAVILY_API_KEY`
- `SECURITY_MASTER_BACKEND`, `SECURITY_MASTER_SQLITE_PATH`
- `DATABASE_URL`
- `MCP_TOOL_TIMEOUT_SECONDS`, `PROVIDER_CALL_TIMEOUT_SECONDS`

完整示例见：

- [`/Users/huweihua/java/qiuzhao/stock-mcp/.env.example`](/Users/huweihua/java/qiuzhao/stock-mcp/.env.example)

### 测试

```bash
uv run pytest
```

### 项目目录

```text
stock-mcp/
├── src/server/api/             # FastAPI HTTP 路由
├── src/server/mcp/             # MCP server 与 tools
├── src/server/domain/          # adapter / service / router
├── src/server/infrastructure/  # Redis / Postgres / 外部连接
├── src/server/core/            # 配置、依赖注入、启动流程
├── docs/                       # 补充文档
└── tests/                      # pytest 测试
```

### License

MIT

---

## English

### What it is

`stock-mcp` is an open-source financial data service with two public interfaces:

- `MCP` for AI agents such as Claude Desktop, Cursor, and DeerFlow
- `HTTP API` for regular backends, scripts, and service integrations

It is designed to provide reliable market data and analysis tools, not investment advice.

### One-command start

```bash
git clone https://github.com/yourusername/stock-mcp.git
cd stock-mcp
cp .env.example .env
docker compose up -d --build
```

Default endpoints:

- `http://127.0.0.1:9898/health`
- `http://127.0.0.1:9898/docs`
- `http://127.0.0.1:9898/mcp`

Default services:

- `stock-mcp`
- `redis`
- `postgres`

Default host ports:

- `stock-mcp`: `127.0.0.1:9898`
- `postgres`: `127.0.0.1:15432`

### Main capabilities

- Market data across A-shares, US equities, ETFs, indices, and crypto
- Technical indicators such as RSI, MACD, moving averages, support/resistance, candlestick patterns
- Fundamentals, money flow, chip analysis, filings, and Tavily-backed news search
- Agent-friendly MCP tools and code-export endpoints

### Public interfaces

HTTP route groups:

- `/api/v1/market/*`
- `/api/v1/technical/*`
- `/api/v1/fundamental/*`
- `/api/v1/money-flow/*`
- `/api/v1/news/*`
- `/api/v1/filings/*`
- `/api/v1/code-export/*`
- `/health`
- `/docs` / `/redoc`

MCP endpoint:

- `/mcp`

### Architecture

```text
MCP clients / HTTP apps
          |
          v
  Thin transports (HTTP / MCP)
          |
          v
Runtime substrate
auth / proxy / lifecycle / registries
          |
          v
Capability plugins <-> Provider plugins
          |
          v
Redis / Postgres
```

The runtime/plugin refactor is complete. Internal layering:

- `src/server/runtime/`
  - stable substrate for auth, proxy policy, provider/capability registry, lifecycle, and health
- `src/server/capabilities/`
  - business capability plugins such as `market`, `filings`, `news`, `money_flow`, `code_export`
- `src/server/providers/`
  - provider plugins declaring which contracts they implement
- `src/server/transports/`
  - thin HTTP/MCP protocol adapters
- `src/server/domain/`
  - symbol resolution and lower-level domain services behind the runtime/provider facade surface; not an extension entrypoint

Only heavy internal logic remains in `src/server/domain/services/`; lightweight capabilities stay capability-local by default.

Extension rules:

- new capabilities should be added as capability plugins
- new data sources should be added as provider plugins
- HTTP and MCP surfaces are composed from the same capability registry

### Authentication modes

- `none`: local development and quick trial
- `token`: static Bearer token for personal or intranet deployment
- `jwt`: production mode using OIDC / JWKS compatible JWT verification

### Proxy boundary

Explicit proxy for foreign providers:

- `Yahoo / yfinance`
- `Finnhub`
- `Futures`
- `Alpha Vantage`
- `Twelve Data`
- `FRED`
- `CCXT`
- `Crypto`
- `EDGAR`

Direct connection for domestic providers:

- `Tushare`
- `Akshare`
- `Baostock`

In Docker mode, `PROXY_HOST` is overridden to `host.docker.internal` by default. Override it with
`DOCKER_PROXY_HOST` when needed.

### Run modes

#### Local uv

```bash
uv sync --dev
cp .env.example .env
export STOCK_MCP_AUTH_MODE=none
uv run python -m uvicorn src.server.app:app --host 127.0.0.1 --port 9898
```

#### Docker Compose

```bash
cp .env.example .env
docker compose up -d --build
```

If you previously used different database settings or stale volumes exist:

```bash
docker compose down -v
docker compose up -d --build
```

#### stdio MCP

```bash
uv run python -c "import src.server.mcp.server as m; m.create_mcp_server().run(transport='stdio')"
```

### Example calls

HTTP:

```bash
curl -X POST http://127.0.0.1:9898/api/v1/technical/indicators/calculate \
  -H "Content-Type: application/json" \
  -d '{
    "symbol": "SSE:600519",
    "period": "6mo",
    "interval": "1d"
  }'
```

MCP:

```bash
curl -X POST http://127.0.0.1:9898/mcp \
  -H "Content-Type: application/json" \
  -d '{
    "jsonrpc": "2.0",
    "method": "tools/call",
    "params": {
      "name": "get_kline_data",
      "arguments": {
        "symbol": "SSE:600519"
      }
    },
    "id": "1"
  }'
```

### Repository layout

```text
stock-mcp/
├── src/server/api/
├── src/server/mcp/
├── src/server/domain/
├── src/server/infrastructure/
├── src/server/core/
├── docs/
└── tests/
```

### License

MIT
