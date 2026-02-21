# CryptoBot

加密货币永续合约 AI 量化交易系统。

AI 多角色工作流自动分析市场、生成交易信号，通过 Freqtrade 自动执行。支持 16 个数据源、8 个 AI 角色、10 个交易对，每 30 分钟自动运行一轮分析。

## 工作原理

```
AI 工作流 (每 30min)              实时监控 (持续)              Freqtrade (5m K线)
─────────────────              ──────────────              ────────────────
collect → screen →   ─写入→   pending_signals.json
analyze → research →           价格进入 entry_range?
trade → risk_review →          5m 指标确认?
execute                        └─写入→ signal.json  ─读取→  自动开仓/平仓
```

**7 个节点的 LangGraph 状态图**：采集数据 → 筛选币种 → 4 分析师分析 → 多空研究员辩论 → 交易决策 → 风控审核 → 写入信号。每轮约 40 次 LLM 调用。

## 特性

- **AI 多角色协作** — 技术分析师、链上分析师、情绪分析师、基本面分析师、看多/看空研究员、交易员、风控经理
- **16 个数据源** — 链上(CoinGlass)、情绪(Fear&Greed)、新闻、订单簿、期权、稳定币流、DXY、DeFi TVL、巨鲸追踪等
- **市场状态感知** — 趋势/震荡/高波动自动识别，差异化参数 + Prompt 注入
- **资金感知策略** — 根据账户余额自动调整（micro/small/medium/large 四档），小账户更保守
- **自动进化** — 绩效驱动 Prompt 迭代、分析师动态权重、多模型竞赛
- **全链路风控** — 最大杠杆/仓位/方向/相关性硬性限制 + AI 软审核
- **Telegram 通知** — 信号/告警/日报/摘要，极端市场无信号也推送分析摘要
- **Web Dashboard** — FastAPI + HTMX 实时面板 + K 线图

## 快速开始

### 前置要求

- Python 3.12
- [uv](https://docs.astral.sh/uv/) (包管理)
- [TA-Lib](https://ta-lib.org/) C 库

### 安装

```bash
# 克隆项目
git clone <repo-url> && cd bian

# 安装依赖
uv sync

# 配置环境变量
cp .env.example .env
# 编辑 .env 填入 API key (至少需要 BINANCE_API_KEY 和 DEEPSEEK_API_KEY)

# 环境健康检查
uv run cryptobot doctor
```

### 运行

```bash
# 手动运行一次分析
uv run cryptobot workflow run

# 启动后台调度 (30min 分析 + 5min 告警 + 4h 复审)
uv run cryptobot daemon start --run-now

# 查看信号
uv run cryptobot signal show

# 查看绩效统计
uv run cryptobot journal stats
```

### Docker 部署 (Freqtrade + Dashboard)

```bash
docker compose build
docker compose up -d
```

## 环境变量

| 变量 | 必需 | 说明 |
|------|------|------|
| `BINANCE_API_KEY` | 是 | Binance API Key (只需读权限) |
| `BINANCE_API_SECRET` | 是 | Binance API Secret |
| `DEEPSEEK_API_KEY` | 是 | DeepSeek API Key (或其他 OpenAI 兼容 API) |
| `COINGLASS_API_KEY` | 否 | 链上/衍生品数据 |
| `CRYPTONEWS_API_KEY` | 否 | 新闻数据 |
| `FINNHUB_API_KEY` | 否 | 宏观经济日历 |
| `COINGECKO_DEMO_KEY` | 否 | CoinGecko 数据 |
| `WHALE_ALERT_API_KEY` | 否 | 巨鲸追踪 (付费) |
| `TELEGRAM_BOT_TOKEN` | 否 | Telegram 通知 |
| `TELEGRAM_CHAT_ID` | 否 | Telegram 通知 |

未配置的可选 API 会静默跳过，不影响核心流程。

## CLI 命令

```bash
cryptobot workflow run [--json-output]     # AI 分析工作流
cryptobot workflow re-review               # 持仓 AI 复审
cryptobot signal show                      # 查看信号
cryptobot monitor check-alerts             # 检查告警
cryptobot daemon start [--run-now]         # 后台调度
cryptobot journal show                     # 交易记录
cryptobot journal stats                    # 绩效统计
cryptobot journal sync                     # 同步 Freqtrade 平仓
cryptobot realtime start                   # 实时入场监控
cryptobot events start                     # 价格异动监控
cryptobot backtest evaluate                # 信号回测评估
cryptobot backtest replay <signal_id>      # K 线复盘
cryptobot prompt list                      # Prompt 版本管理
cryptobot web start [--port 8000]          # Web Dashboard
cryptobot doctor                           # 环境健康检查
cryptobot init                             # 环境初始化
```

## 项目结构

```
src/cryptobot/
├── workflow/              # AI 分析工作流
│   ├── graph.py           #   LangGraph 状态图 + 条件路由
│   ├── nodes/             #   7 个节点 (collect/screen/analyze/research/trade/risk/execute)
│   ├── prompts.py         #   8 个 AI 角色 system prompt
│   ├── llm.py             #   Claude CLI 子进程封装
│   ├── api_llm.py         #   OpenAI 兼容 API 后端
│   └── re_review.py       #   持仓复审流程
├── data/                  # 16 个外部数据源
│   ├── onchain.py         #   CoinGlass 链上数据
│   ├── sentiment.py       #   Fear & Greed 指数
│   ├── news.py            #   新闻数据
│   ├── orderbook.py       #   Binance 订单簿
│   ├── options.py         #   Deribit 期权
│   ├── dxy.py             #   DXY 美元指数
│   ├── defi_tvl.py        #   DeFi TVL
│   ├── whale_tracker.py   #   巨鲸追踪
│   └── ...                #   稳定币/储备/宏观/稀释等
├── indicators/            # 技术指标
│   ├── calculator.py      #   TA-Lib 指标计算
│   └── multi_timeframe.py #   多时间框架共振
├── evolution/             # 自动进化引擎
│   ├── prompt_manager.py  #   Prompt 版本管理
│   ├── prompt_optimizer.py#   绩效驱动自动优化
│   ├── regime_prompts.py  #   市场状态 Prompt Addon
│   ├── capital_prompts.py #   资金层级 Prompt Addon
│   └── model_competition.py#  多模型竞赛
├── signal/                # 信号读写
├── realtime/              # 实时入场监控 + WebSocket
├── events/                # 价格异动监控
├── risk/                  # 仓位计算 + 爆仓距离
├── journal/               # 交易日志 + 绩效分析
├── web/                   # Dashboard (FastAPI + HTMX)
├── cli/                   # 16 个 CLI 子命令
├── capital_strategy.py    # 资金感知策略
├── regime_smoother.py     # 市场状态平滑
├── notify.py              # Telegram 通知
└── config.py              # 配置加载

config/
├── settings.yaml          # 全局配置 (风控/调度/LLM/通知)
└── pairs.yaml             # 10 个交易对配置

freqtrade_strategies/
└── AgentSignalStrategy.py # Freqtrade 策略 (动态止损/分批止盈)
```

## 配置

核心配置在 `config/settings.yaml`，关键项：

| 配置 | 默认 | 说明 |
|------|------|------|
| `risk.max_leverage` | 5 | 最大杠杆 |
| `risk.max_single_position_pct` | 25% | 单币种最大仓位 |
| `risk.max_loss.per_trade_pct` | 2% | 单笔最大亏损 |
| `schedule.full_cycle_minutes` | 30 | 分析周期 (分钟) |
| `llm.provider` | "api" | LLM 后端 ("claude" / "api") |
| `realtime.enabled` | false | 实时入场监控 |
| `telegram.enabled` | true | Telegram 通知 |

修改 `settings.yaml` 后无需重启 daemon，调度器每 2 分钟自动热更新。

支持多种 LLM 后端，在 `llm.api` 中切换：

```yaml
# DeepSeek (默认)
llm:
  provider: "api"
  api:
    base_url: "https://api.deepseek.com/v1"
    api_key_env: "DEEPSEEK_API_KEY"

# 也支持 OpenAI / Groq / 智谱 / Ollama 等 OpenAI 兼容 API
```

## 交易对

10 个 USDT 永续合约，定义在 `config/pairs.yaml`：

BTC, ETH, SOL, XRP, BNB, ADA, DOGE, AVAX, LINK, SUI

含杠杆范围、类别分组、BTC 相关性分组，用于组合风控。

## 部署

### VPS 部署

```bash
# 安装 TA-Lib (Ubuntu)
sudo apt install build-essential python3-dev wget
wget https://github.com/ta-lib/ta-lib/releases/download/v0.6.4/ta-lib-0.6.4-src.tar.gz
tar xzf ta-lib-0.6.4-src.tar.gz && cd ta-lib-0.6.4 && ./configure && make && sudo make install

# 安装 uv
curl -LsSf https://astral.sh/uv/install.sh | sh

# 部署代码 + 启动
uv sync
cp .env.example .env  # 编辑填入 API key
nohup uv run cryptobot daemon start --run-now > daemon.log 2>&1 &
```

### systemd 服务

```bash
sudo cp deploy/cryptobot-daemon.service /etc/systemd/system/
sudo systemctl enable cryptobot-daemon
sudo systemctl start cryptobot-daemon
```

## 测试

```bash
uv run pytest                              # 全部测试
uv run pytest tests/test_workflow.py -v    # 工作流测试
uv run ruff check src/                     # Lint 检查
```

## 技术栈

- **Python 3.12** + uv + hatchling
- **LangGraph** — AI 工作流状态图
- **TA-Lib** — 技术指标计算
- **FastAPI** — Web Dashboard + API
- **APScheduler** — 定时任务调度
- **Freqtrade** — 交易执行引擎
- **httpx** — HTTP 客户端
- **Rich** — CLI 输出美化

## License

Private — All rights reserved.
