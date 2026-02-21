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
uv run cryptobot daemon start                 # 启动调度器 (30min分析+5min告警+4h复审)
uv run cryptobot daemon start --run-now       # 启动调度器并立即运行一次分析
uv run cryptobot journal show                 # 查看交易记录
uv run cryptobot journal stats                # 查看绩效统计 (胜率/盈亏比/置信度校准)
uv run cryptobot journal sync                 # 从 Freqtrade 同步已平仓交易
uv run cryptobot prompt list                  # 列出 prompt 版本
uv run cryptobot prompt show                  # 查看当前活跃版本详情
uv run cryptobot prompt activate v1.1         # 切换 prompt 版本
uv run cryptobot events start                 # 启动价格异动监控
uv run cryptobot events status                # 查看事件监控配置
uv run cryptobot archive list                  # 列出决策归档
uv run cryptobot archive show <run_id>         # 查看完整归档
uv run cryptobot archive history BTCUSDT       # 币种决策历史
uv run cryptobot archive cleanup --keep-months 3  # 清理旧归档
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
AI 工作流 (30min 周期)                实时监控 (持续)              Freqtrade (5m K线)
─────────────────                ──────────────              ────────────────
collect → screen →    ─写入→    pending_signals.json
analyze → research →            轮询 Binance (10s)
trade → risk_review →           价格进入 entry_range?
execute                         5m 指标确认?
                                └─写入→ signal.json  ─读取→  AgentSignalStrategy
```

当 `config/settings.yaml` 中 `realtime.enabled: false` 时，execute 节点直接写 signal.json（跳过实时监控）。

### LLM 调用方式

通过 `claude -p` 子进程调用 Claude Code 订阅额度（`workflow/llm.py`），**不需要 Anthropic API key**。支持 OpenAI 兼容 API 后端（`workflow/api_llm.py`），通过 `llm.provider` 切换。工作流每轮约 40 次调用：
- 4 分析师 × 5 币 = 20 个 haiku
- 2 研究员 × 5 币 = 10 个 sonnet
- 5 交易决策 + 5 风控审核 = 10 个 sonnet

**角色级模型选择**: `call_claude()` / `call_api()` 支持 `role` 参数，可在 `settings.yaml` 的 `llm.api.role_models` 中按角色指定不同模型（如 trader 用 reasoner，analyst 用 chat）。未配置时行为不变。

### 模块职责

| 模块 | 职责 |
|------|------|
| `workflow/graph.py` | LangGraph 7 节点状态图 + 独立 `re_review()` 持仓复审流程 |
| `workflow/llm.py` | Claude CLI 子进程封装，内置速率限制和重试，支持 `role` 参数路由模型 |
| `workflow/api_llm.py` | OpenAI 兼容 API 后端（DeepSeek/OpenAI/Groq 等），支持角色级模型选择 + token 用量追踪 |
| `workflow/prompts.py` | 7 个 AI 角色的 system prompt + JSON schema（含 RE_REVIEWER） |
| `signal/bridge.py` | signal.json / pending_signals.json 读写校验 + `update_signal_field` 动态更新 |
| `realtime/monitor.py` | 轮询 Binance 价格，等待入场区间 + 5m 指标确认后 promote 信号 |
| `indicators/calculator.py` | 技术指标计算（TA-Lib），K 线数据加载（feather 优先 + Binance API fallback） |
| `indicators/multi_timeframe.py` | 多时间框架共振、量价分析、支撑阻力 |
| `data/` | 外部数据获取：链上(CoinGlass)、情绪(Fear&Greed)、新闻(CryptoNews-API)、稳定币流(DefiLlama)、订单簿(Binance)、交易所储备(CoinGlass)、宏观日历(FinnHub)、期权(Deribit)、代币稀释(CoinGecko)、DXY美元指数(Yahoo Finance)、DeFi TVL(DefiLlama)、巨鲸追踪(Whale Alert) |
| `regime_smoother.py` | 市场状态转换平滑：连续 N 周期确认才切换 regime，防止边界反复跳动 |
| `capital_strategy.py` | 资金感知策略：根据余额自动调整层级(micro/small/medium/large)，与 regime 正交叠加取更严格值 |
| `evolution/prompt_manager.py` | Prompt 版本管理：版本化存储/切换/对比 addon，持久化 `prompt_versions.json` |
| `evolution/regime_prompts.py` | Regime 级 Prompt Addon：趋势市/震荡市/高波动市分别注入不同偏好到 trader/analyst |
| `evolution/capital_prompts.py` | 资金层级 Prompt Addon：micro/small 层级注入保守偏好到 trader/analyst/risk_manager |
| `evolution/prompt_optimizer.py` | 绩效驱动 Prompt 自动迭代：检测退化 → 分析失败 → AI 生成改进 → 创建新版本 |
| `evolution/model_competition.py` | 多模型竞赛：并行调用多模型决策，consensus/best_performer 策略择优 |
| `risk/` | 仓位计算(Kelly)、爆仓距离计算 |
| `notify.py` | Telegram 通知：信号/风控/告警/日报/错误推送（silent fallback） |
| `journal/` | 交易记录与绩效：SignalRecord 生命周期(含 model_id) + 胜率/盈亏比/置信度校准 + prompt 注入 + 分析师动态权重 + 动态置信度阈值 |
| `events/` | 价格异动监控：30s 轮询检测 5min/15min 大幅波动 → 紧急复审 + 通知 |
| `cli/scheduler.py` | APScheduler 调度器：8 个定时任务(含日报 cron + prompt 自动优化) + 可选事件监控线程 |
| `cli/prompt.py` | Prompt 版本管理 CLI：list/new-version/activate/show |
| `backtest/evaluator.py` | 信号回测评估：胜率/盈亏比/连胜连败 + K 线复盘(MFE/MAE) |
| `cli/doctor.py` | 12 项环境健康检查（Python/TA-Lib/API/目录等） |
| `cli/init_cmd.py` | 环境初始化：创建目录 + .env + 交互 API key + doctor |
| `archive/` | AI 决策归档：每轮工作流保存完整决策链(筛选评分/分析/风控细节/信号)到 JSON，支持 CLI 查阅 |
| `cli/` | Click 命令组，17 个子命令 |
| `web/routes/api.py` | Dashboard API：仪表盘/信号/持仓/告警/绩效 + K 线数据 + 交易历史 |
| `freqtrade_strategies/AgentSignalStrategy.py` | Freqtrade 策略：动态止损(含 Agent 尾随)、分批止盈(adjust_trade_position)、仓位控制 |

### 数据文件路径约定

- Freqtrade K 线 feather: `user_data/data/binance/futures/{BASE}_USDT_USDT-{tf}-futures.feather`（备选路径 `user_data/data/futures/`）
- 信号输出: `data/output/signals/signal.json`、`pending_signals.json`
- 缓存: `data/output/.cache/`（各数据源子目录: `stablecoin/`、`exchange_reserve/`、`orderbook/`、`coinglass/`、`dxy/`、`defi_tvl/`、`whale/` 等）
- 分析师权重: `data/output/evolution/weights.json`
- Regime 历史: `data/output/evolution/regime_history.json`
- Prompt 版本: `data/output/evolution/prompt_versions.json`
- Prompt 迭代记录: `data/output/evolution/iterations.json`
- 模型竞赛记录: `data/output/evolution/competition.json`
- 决策归档: `data/output/archive/{YYYY-MM}/{run_id}.json`
- 配置: `config/settings.yaml`、`config/pairs.yaml`

### 交易对配置

10 个币种在 `config/pairs.yaml` 中定义，含杠杆范围、类别、相关性分组。`config.get_all_symbols()` 返回所有交易对。

## 关键约定

- Python 3.12，Ruff line-length=100
- 包管理用 uv（hatchling 构建）
- 信号文件原子写入：先写 `.json.tmp` 再 rename
- 交易对格式：代码中用 Binance 格式 `BTCUSDT`，Freqtrade 用 `BTC/USDT:USDT`
- 交易记录路径: `data/output/journal/records.json`
- 环境变量：`BINANCE_API_KEY`、`BINANCE_API_SECRET`、`COINGLASS_API_KEY`、`CRYPTONEWS_API_KEY`、`COINGECKO_DEMO_KEY`、`DEEPSEEK_API_KEY`、`FINNHUB_API_KEY`、`WHALE_ALERT_API_KEY`(可选)、`TELEGRAM_BOT_TOKEN`、`TELEGRAM_CHAT_ID`
- 测试中网络请求全部 mock，标记 `@pytest.mark.network` 的测试需要真实网络
