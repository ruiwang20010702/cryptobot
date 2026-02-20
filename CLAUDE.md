# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 项目概述

加密货币永续合约 AI 量化交易系统。AI 多角色工作流分析市场 → 生成交易信号 → Freqtrade 自动执行。

## 常用命令

```bash
# 安装依赖
uv sync

# 运行全部测试
uv run pytest

# 运行单个测试文件/用例
uv run pytest tests/test_signal_bridge.py -v
uv run pytest tests/test_workflow.py::TestScreenNode::test_selects_top_5 -v

# Lint
uv run ruff check src/

# CLI 入口
uv run cryptobot --help

# 常用 CLI
uv run cryptobot workflow run --json-output   # 运行完整 AI 分析工作流
uv run cryptobot workflow re-review           # 持仓 AI 复审（调整止损）
uv run cryptobot realtime start               # 启动实时入场监控
uv run cryptobot realtime status              # 查看 pending 信号
uv run cryptobot signal show                  # 查看当前信号
uv run cryptobot monitor check-alerts         # 检查持仓告警
uv run cryptobot daemon start                 # 启动调度器 (2h分析+5min告警+4h复审)
uv run cryptobot daemon start --run-now       # 启动调度器并立即运行一次分析
uv run cryptobot journal show                 # 查看交易记录
uv run cryptobot journal stats                # 查看绩效统计 (胜率/盈亏比/置信度校准)
uv run cryptobot journal sync                 # 从 Freqtrade 同步已平仓交易
uv run cryptobot events start                 # 启动价格异动监控
uv run cryptobot events status                # 查看事件监控配置
uv run cryptobot backtest evaluate            # 评估近30天信号质量
uv run cryptobot backtest evaluate --json-output  # JSON 格式输出
uv run cryptobot backtest replay <signal_id>  # 单信号 K 线复盘
uv run cryptobot doctor                       # 环境健康检查 (12项)
uv run cryptobot doctor --json-output         # JSON 格式健康检查
uv run cryptobot init                         # 初始化运行环境 (创建目录+.env+doctor)

# Docker (Freqtrade + Dashboard)
docker compose build                          # 构建镜像
docker compose up -d                          # 启动服务
```

## 架构

### 核心信号流

```
AI 工作流 (2h 周期)                实时监控 (持续)              Freqtrade (5m K线)
─────────────────                ──────────────              ────────────────
collect → screen →    ─写入→    pending_signals.json
analyze → research →            轮询 Binance (10s)
trade → risk_review →           价格进入 entry_range?
execute                         5m 指标确认?
                                └─写入→ signal.json  ─读取→  AgentSignalStrategy
```

当 `config/settings.yaml` 中 `realtime.enabled: false` 时，execute 节点直接写 signal.json（跳过实时监控）。

### LLM 调用方式

通过 `claude -p` 子进程调用 Claude Code 订阅额度（`workflow/llm.py`），**不需要 Anthropic API key**。工作流每轮约 40 次调用：
- 4 分析师 × 5 币 = 20 个 haiku
- 2 研究员 × 5 币 = 10 个 sonnet
- 5 交易决策 + 5 风控审核 = 10 个 sonnet

### 模块职责

| 模块 | 职责 |
|------|------|
| `workflow/graph.py` | LangGraph 7 节点状态图 + 独立 `re_review()` 持仓复审流程 |
| `workflow/llm.py` | Claude CLI 子进程封装，内置速率限制和重试 |
| `workflow/prompts.py` | 7 个 AI 角色的 system prompt + JSON schema（含 RE_REVIEWER） |
| `signal/bridge.py` | signal.json / pending_signals.json 读写校验 + `update_signal_field` 动态更新 |
| `realtime/monitor.py` | 轮询 Binance 价格，等待入场区间 + 5m 指标确认后 promote 信号 |
| `indicators/calculator.py` | 技术指标计算（TA-Lib），K 线数据加载（feather 优先 + Binance API fallback） |
| `indicators/multi_timeframe.py` | 多时间框架共振、量价分析、支撑阻力 |
| `data/` | 外部数据获取：链上(CoinGlass)、情绪(Fear&Greed)、新闻(CryptoNews-API) |
| `risk/` | 仓位计算(Kelly)、爆仓距离计算 |
| `notify.py` | Telegram 通知：信号/风控/告警/日报/错误推送（silent fallback） |
| `journal/` | 交易记录与绩效：SignalRecord 生命周期 + 胜率/盈亏比/置信度校准 + prompt 注入 |
| `events/` | 价格异动监控：30s 轮询检测 5min/15min 大幅波动 → 紧急复审 + 通知 |
| `cli/scheduler.py` | APScheduler 调度器：7 个定时任务(含日报 cron) + 可选事件监控线程 |
| `backtest/evaluator.py` | 信号回测评估：胜率/盈亏比/连胜连败 + K 线复盘(MFE/MAE) |
| `cli/doctor.py` | 12 项环境健康检查（Python/TA-Lib/API/目录等） |
| `cli/init_cmd.py` | 环境初始化：创建目录 + .env + 交互 API key + doctor |
| `cli/` | Click 命令组，15 个子命令 |
| `freqtrade_strategies/AgentSignalStrategy.py` | Freqtrade 策略：动态止损(含 Agent 尾随)、分批止盈(adjust_trade_position)、仓位控制 |

### 数据文件路径约定

- Freqtrade K 线 feather: `user_data/data/binance/futures/{BASE}_USDT_USDT-{tf}-futures.feather`（备选路径 `user_data/data/futures/`）
- 信号输出: `data/output/signals/signal.json`、`pending_signals.json`
- 缓存: `data/output/.cache/`
- 配置: `config/settings.yaml`、`config/pairs.yaml`

### 交易对配置

10 个币种在 `config/pairs.yaml` 中定义，含杠杆范围、类别、相关性分组。`config.get_all_symbols()` 返回所有交易对。

## 关键约定

- Python 3.12，Ruff line-length=100
- 包管理用 uv（hatchling 构建）
- 信号文件原子写入：先写 `.json.tmp` 再 rename
- 交易对格式：代码中用 Binance 格式 `BTCUSDT`，Freqtrade 用 `BTC/USDT:USDT`
- 交易记录路径: `data/output/journal/records.json`
- 环境变量：`BINANCE_API_KEY`、`BINANCE_API_SECRET`、`COINGLASS_API_KEY`、`CRYPTONEWS_API_KEY`、`COINGECKO_DEMO_KEY`、`TELEGRAM_BOT_TOKEN`、`TELEGRAM_CHAT_ID`
- 测试中网络请求全部 mock，标记 `@pytest.mark.network` 的测试需要真实网络
