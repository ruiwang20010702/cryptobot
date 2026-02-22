# CryptoBot — 加密货币永续合约 AI 量化交易系统

> 版本: 2026-02-22 | 914 tests passed | 16,000+ LOC | Lint clean

AI 多角色工作流分析市场 → 生成交易信号 → Freqtrade 自动执行。

---

## 一、系统概览

### 1.1 核心数据流

```
AI 工作流 (每 2h)                实时监控 (持续)              Freqtrade (5m K线)
─────────────────              ──────────────              ────────────────
collect → screen →   ─写入→   pending_signals.json
analyze → research →           WebSocket 实时价格
trade → risk_review →          价格进入 entry_range?
execute                        5m 指标确认?
                               └─写入→ signal.json  ─读取→  AgentSignalStrategy
                                                            - 动态止损 (4级)
价格异动监控 (30s)                                           - 分批止盈
────────────────                                            - 仓位控制
5min 涨跌幅 > 3%?                                           - Agent 尾随止损
15min 涨跌幅 > 5%?
└→ 紧急复审 + Telegram 告警

Web Dashboard (FastAPI + HTMX + lightweight-charts)
────────────────────────────────────────────────────
持仓/信号/绩效 面板 + K 线图 + 交易历史 ← /api/* JSON API

自动进化引擎 (每日)
────────────────────
绩效退化检测 → 失败案例分析 → AI 生成改进 Prompt → 自动激活新版本
多模型竞赛 → consensus / best_performer 策略择优执行
策略顾问 → 绩效模式发现 → 规则生成 → 30天评估 → 续期/淘汰

量化回测引擎 (按需)
────────────────────
历史信号 → 成本模型(手续费+滑点+资金费率) → 逐根K线模拟 → 净值曲线
→ Sharpe/Sortino/MaxDD/Calmar → 随机/MA/RSI/布林基线对照 → 统计检验
历史回放 → 90天×5币种 → LLM 批量生成 197 信号 → 全面碾压基线 (Sharpe 7.06)
```

### 1.2 架构图

```
┌─────────────────────────────────────────────────────────────────────────┐
│                        AI 分析工作流 (LangGraph 7 节点)                   │
│                                                                         │
│  collect_data ──→ screen ──→ analyze (4分析师×5币=20 haiku)              │
│      │               │           │  + regime addon 注入                  │
│  10币种数据采集   regime 检测  research (2研究员×5币=10 sonnet)              │
│  技术+链上+情绪   + 平滑器      │                                        │
│  +订单簿+期权               trade (5 sonnet) ← 持仓上下文+绩效反馈        │
│  +宏观+DXY+TVL                 │  + prompt addon + regime 偏好           │
│  +巨鲸+稀释                    │  + 多模型竞赛 (可选)                     │
│                            risk_review (5 sonnet) ← 硬性风控规则         │
│                                  │                                      │
│                              execute ──→ signal.json / pending          │
│                                  │  + model_id + prompt_version         │
└─────────────────────────────────────────────────────────────────────────┘
                                   │
              ┌────────────────────┼──────────────────────┐
              ▼                    ▼                      ▼
   ┌──────────────────┐  ┌─────────────────┐  ┌──────────────────┐
   │ WebSocket 价格流  │  │  实时入场监控     │  │  价格异动事件     │
   │ Binance miniTicker│  │  entry_range     │  │  5min/15min 阈值  │
   │ 全币种实时推送    │  │  + 5m 指标确认    │  │  紧急复审+通知    │
   └──────────────────┘  └─────────────────┘  └──────────────────┘
              │                    │                      │
              ▼                    ▼                      ▼
   ┌──────────────────────────────────────────────────────────────┐
   │                    Freqtrade (AgentSignalStrategy)            │
   │  custom_stoploss: Agent止损 → 尾随止盈 → 三档移动止盈 → 默认  │
   │  adjust_trade_position: 分批止盈减仓                          │
   │  custom_exit: 最终止盈全仓平仓                                │
   │  custom_stake_amount: AI 仓位控制 (Kelly + 固定风险法)        │
   │  leverage: AI 杠杆控制 (硬上限 5x)                           │
   └──────────────────────────────────────────────────────────────┘
              │
              ▼
   ┌──────────────────┐     ┌──────────────────┐     ┌──────────────────┐
   │  交易日志 Journal │     │  Telegram 通知    │     │  自动进化引擎     │
   │  信号全生命周期    │     │  信号/告警/日报    │     │  Prompt 版本管理   │
   │  绩效/胜率/校准    │     │  爆仓预警/异常     │     │  绩效驱动优化     │
   │  分析师动态权重    │     │  Prompt 优化通知   │     │  多模型竞赛       │
   └──────────────────┘     └──────────────────┘     └──────────────────┘
```

### 1.3 LLM 调用方式

通过 `claude -p` 子进程调用 Claude Code 订阅额度（`workflow/llm.py`），**不需要 Anthropic API key**。支持 OpenAI 兼容 API 后端（`workflow/api_llm.py`），通过 `llm.provider` 切换。支持角色级模型选择和多模型竞赛。每轮约 40 次调用：

| 角色 | 模型 | 数量 | 说明 |
|------|------|------|------|
| 4 分析师 | haiku | ×5 币 = 20 | 技术/链上/情绪/基本面 + regime addon |
| 2 研究员 | sonnet | ×5 币 = 10 | 看多/看空论证 |
| 交易员 | sonnet | ×5 = 5 | 综合决策 + prompt addon + regime 偏好 |
| 风控经理 | sonnet | ×5 = 5 | 审核+硬性规则 |

竞赛模式下交易决策额外调用 N 个模型 (N = 竞赛模型数)。

---

## 二、模块职责

### 2.1 核心模块

| 模块 | 文件 | 职责 |
|------|------|------|
| **AI 工作流** | `workflow/graph.py` + `nodes/*.py` | LangGraph 7 节点状态图，拆分为 8 个独立模块 |
| **持仓复审** | `workflow/re_review.py` | 独立 `re_review()` 流程，AI 重新评估持仓并调整止损 |
| **工作流辅助** | `workflow/utils.py` + `state.py` | 公共数据获取函数 + WorkflowState 类型定义 |
| **LLM 封装** | `workflow/llm.py` | Claude CLI 子进程调用，内置速率限制和重试，支持 role 参数 |
| **API LLM** | `workflow/api_llm.py` | OpenAI 兼容 API 后端，角色级模型选择 + token 用量追踪 |
| **角色 Prompt** | `workflow/prompts.py` | 8 个 AI 角色 system prompt + JSON schema + 动态版本管理 |
| **信号管理** | `signal/bridge.py` | signal.json / pending 原子写入校验 + `update_signal_field` 动态更新 |
| **实时监控** | `realtime/monitor.py` | 等待入场区间 + 5m 指标确认后 promote 信号 |
| **WebSocket 价格流** | `realtime/ws_price_feed.py` | Binance miniTicker 实时推送，线程安全缓存，自动重连 |
| **价格异动事件** | `events/price_monitor.py` | 30s 轮询检测 5min/15min 大幅波动 → 紧急复审 + 通知 |
| **事件分发** | `events/dispatcher.py` | 事件过滤、通知、触发复审 |
| **技术指标** | `indicators/calculator.py` | TA-Lib 全指标计算 + K 线数据加载（feather 优先 + Binance API fallback） |
| **多时间框架** | `indicators/multi_timeframe.py` | 1h/4h/1d 共振分析 + 量价分析 + 支撑阻力 |
| **加密特有指标** | `indicators/crypto_specific.py` | 资金费率/OI/多空比/清算数据整合 |
| **市场结构** | `indicators/market_structure.py` | 结构分析 |
| **Regime 平滑** | `regime_smoother.py` | 市场状态转换平滑：连续 N 周期确认才切换，防止边界跳动 |

### 2.2 数据模块 (16 个数据源)

| 模块 | 文件 | 数据源 |
|------|------|--------|
| **链上数据** | `data/onchain.py` | CoinGlass 资金费率/OI/多空比 |
| **情绪数据** | `data/sentiment.py` | Fear&Greed Index |
| **新闻数据** | `data/news.py` + `crypto_news.py` | CryptoNews-API + CoinGecko |
| **清算数据** | `data/liquidation.py` | CoinGlass 清算热力图 |
| **订单簿深度** | `data/orderbook.py` | Binance Order Book bid/ask 不平衡 |
| **稳定币流** | `data/stablecoin.py` | DefiLlama 稳定币铸造/销毁 |
| **交易所储备** | `data/exchange_reserve.py` | CoinGlass 交易所 BTC/ETH 储备 |
| **宏观日历** | `data/macro_calendar.py` | FinnHub 经济日历 (FOMC/CPI/NFP) |
| **期权数据** | `data/options.py` | Deribit Put/Call 比率/Max Pain/IV |
| **代币稀释** | `data/token_dilution.py` | CoinGecko 供应量/稀释风险 |
| **DXY 美元指数** | `data/dxy.py` | Yahoo Finance DXY 走势 |
| **DeFi TVL** | `data/defi_tvl.py` | DefiLlama 链级 TVL 趋势 |
| **巨鲸追踪** | `data/whale_tracker.py` | Whale Alert 大额转账 |

### 2.3 自动进化模块

| 模块 | 文件 | 职责 |
|------|------|------|
| **Prompt 版本管理** | `evolution/prompt_manager.py` | 版本化存储/切换/对比 addon，`prompt_versions.json` |
| **Regime Prompt** | `evolution/regime_prompts.py` | 趋势/震荡/高波动市分别注入不同偏好到 trader/analyst |
| **自动 Prompt 优化** | `evolution/prompt_optimizer.py` | 绩效退化检测 → 失败分析 → AI 生成改进 → 创建新版本 |
| **多模型竞赛** | `evolution/model_competition.py` | 并行调用多模型决策，consensus/best_performer 择优 |
| **策略顾问** | `evolution/strategy_advisor.py` | 绩效驱动策略规则生成 + 14天有效期 + 自动评估续期/淘汰 |
| **分析师权重** | `journal/analyst_weights.py` | 基于准确率自动升降权，注入 trader prompt |
| **置信度校准** | `journal/confidence_tuner.py` | 动态置信度阈值，基于历史校准 |

### 2.4 交易与风控

| 模块 | 文件 | 职责 |
|------|------|------|
| **仓位计算** | `risk/position_sizer.py` | Kelly + 固定风险法，已接入工作流 |
| **爆仓距离** | `risk/liquidation_calc.py` | 爆仓价格计算 + 预警分级 |
| **Freqtrade API** | `freqtrade_api.py` | 统一 FT REST API 封装 |
| **Freqtrade 策略** | `AgentSignalStrategy.py` | 4 级动态止损 + 分批止盈 + 仓位控制 + Agent 尾随 |

### 2.5 运营模块

| 模块 | 文件 | 职责 |
|------|------|------|
| **交易日志** | `journal/models.py` + `storage.py` | SignalRecord 全生命周期 (含 model_id) |
| **绩效分析** | `journal/analytics.py` | 胜率/盈亏比/置信度校准/分析师准确率/按币种统计 |
| **回测评估** | `backtest/evaluator.py` | 信号回测：胜率/盈亏比/连胜连败 + K 线复盘(MFE/MAE) |
| **成本模型** | `backtest/cost_model.py` | 交易成本建模：手续费/滑点/资金费率，杠杆敏感 |
| **交易模拟器** | `backtest/trade_simulator.py` | 逐根 1h K 线扫描，分批止盈，MFE/MAE，净 PnL |
| **净值追踪** | `backtest/equity_tracker.py` | 净值曲线 + Sharpe/Sortino/MaxDD/Calmar/月度收益 |
| **基线生成器** | `backtest/baselines.py` | 随机/MA交叉/RSI/布林通道 4 种基线信号 |
| **统计检验** | `backtest/stats.py` | Welch's t-test + Permutation test (无 scipy) |
| **回测引擎** | `backtest/engine.py` | 完整回测编排：信号加载→模拟→统计→报告持久化 |
| **历史回放** | `backtest/historical_replay.py` | 纯技术面 LLM 批量生成信号：分页K线下载→逐日快照→并行LLM→交易模拟 |
| **A/B 测试** | `backtest/ab_test.py` | 按 prompt_version 分组对比绩效 |
| **通知系统** | `notify.py` | Telegram 推送：信号/风控/告警/错误/优化通知（silent fallback） |
| **调度器** | `cli/scheduler.py` | APScheduler: 8 个定时任务 + 配置热更新 + WS/监控线程 |
| **Web Dashboard** | `web/app.py` + `routes/*.py` | FastAPI + HTMX + K 线图 + 交易历史，8 个 API 端点 |
| **缓存** | `cache.py` | 文件级缓存，TTL 控制 |

### 2.6 CLI 命令 (16 个子命令组)

```bash
cryptobot workflow run [--json-output]     # 运行完整 AI 分析工作流
cryptobot workflow re-review               # 持仓 AI 复审
cryptobot signal show                      # 查看当前信号
cryptobot monitor check-alerts             # 检查持仓告警
cryptobot portfolio summary                # 持仓概览
cryptobot data onchain/sentiment/news      # 查看外部数据
cryptobot indicator calc                   # 计算技术指标
cryptobot realtime start/status            # 实时入场监控
cryptobot daemon start [--run-now]         # 启动调度器
cryptobot journal show/stats/sync          # 交易记录
cryptobot events start/status              # 价格异动监控
cryptobot backtest evaluate/replay/ab-test # 回测评估
cryptobot backtest run/baseline/compare   # 量化回测 + 基线对比
cryptobot backtest replay-history --days 90 # 历史回放 (LLM 批量生成信号)
cryptobot prompt list/show/activate        # Prompt 版本管理
cryptobot web start [--port 8000]          # Web Dashboard
cryptobot doctor                           # 环境健康检查
cryptobot init                             # 环境初始化
```

---

## 三、数据文件路径

| 文件 | 路径 | 说明 |
|------|------|------|
| 全局配置 | `config/settings.yaml` | 风控/调度/通知/事件/竞赛等参数 |
| 交易对配置 | `config/pairs.yaml` | 10 币种 + 杠杆范围 + 相关性分组 |
| Freqtrade 配置 | `config/freqtrade/config.json` | 实盘配置 |
| Freqtrade dry_run | `config/freqtrade/config_dry_run.json` | 模拟盘配置 (10000 USDT) |
| 信号输出 | `data/output/signals/signal.json` | 活跃信号 |
| 待入场信号 | `data/output/signals/pending_signals.json` | 等待入场的信号 |
| 交易日志 | `data/output/journal/records.json` | 全部交易记录 |
| 分析师权重 | `data/output/evolution/weights.json` | 分析师动态权重 |
| Regime 历史 | `data/output/evolution/regime_history.json` | 市场状态转换记录 |
| Prompt 版本 | `data/output/evolution/prompt_versions.json` | Prompt 版本化存储 |
| Prompt 迭代 | `data/output/evolution/iterations.json` | 自动优化迭代记录 |
| 模型竞赛 | `data/output/evolution/competition.json` | 多模型竞赛结果 |
| 缓存目录 | `data/output/.cache/` | API 响应缓存 (各数据源子目录) |
| K 线数据 | `user_data/data/binance/futures/` | Freqtrade feather 文件 |
| 环境变量模板 | `.env.example` | API key 配置模板 |

---

## 四、配置说明

### 4.1 环境变量

```bash
# 必需
BINANCE_API_KEY=xxx         # Binance API (交易 + 数据)
BINANCE_API_SECRET=xxx

# LLM API (使用 API 模式时)
DEEPSEEK_API_KEY=xxx        # DeepSeek API

# 可选 (数据增强)
COINGLASS_API_KEY=xxx       # 链上/衍生品数据
CRYPTONEWS_API_KEY=xxx      # 新闻数据
COINGECKO_DEMO_KEY=xxx      # CoinGecko 数据
FINNHUB_API_KEY=xxx         # 宏观经济日历
WHALE_ALERT_API_KEY=xxx     # 巨鲸追踪 (可选)

# 可选 (通知)
TELEGRAM_BOT_TOKEN=xxx      # Telegram 通知
TELEGRAM_CHAT_ID=xxx
```

### 4.2 关键配置项 (settings.yaml)

| 配置项 | 默认值 | 说明 |
|--------|--------|------|
| `risk.max_leverage` | 5 | 最大杠杆硬上限 |
| `risk.max_single_position_pct` | 25% | 单币种最大仓位 |
| `risk.max_total_position_pct` | 80% | 总持仓上限 |
| `risk.max_same_direction_pct` | 50% | 同方向总仓位上限 |
| `risk.max_loss.per_trade_pct` | 2% | 单笔最大亏损 |
| `schedule.full_cycle_hours` | 2 | 分析周期间隔 |
| `schedule.monitor_interval_minutes` | 5 | 告警检查间隔 |
| `realtime.enabled` | false | 实时入场监控开关 |
| `events.enabled` | false | 价格异动监控开关 |
| `market_regime.smoothing_cycles` | 2 | Regime 平滑确认周期数 |
| `llm.provider` | "api" | LLM 后端: "claude" 或 "api" |
| `llm.competition.enabled` | false | 多模型竞赛开关 |
| `llm.competition.strategy` | "consensus" | 竞赛策略: consensus / best_performer |

### 4.3 配置热更新

修改 `settings.yaml` 后无需重启 daemon。调度器每 2 分钟检查文件变化，自动重新加载调度间隔。

---

## 五、已完成路线图

### P0 — 资金安全 & 基础自动化

- [x] **仓位计算接入工作流** — `calc_position_size()` 替代硬编码 1000 USDT
- [x] **AI 持仓+账户上下文** — TRADER/RISK_MANAGER prompt 注入余额、持仓、风控规则
- [x] **硬性风控规则** — 代码层面 enforce `max_same_direction_pct` 等限制
- [x] **APScheduler 调度器** — `cryptobot daemon start` 一键启动定时任务

### P1 — 运营闭环

- [x] **Telegram 通知** — 信号/止损调整/爆仓预警/工作流异常/日报推送
- [x] **交易日志 Journal** — SignalRecord 全生命周期 + Freqtrade 同步
- [x] **绩效分析** — 胜率/盈亏比/置信度校准/分析师权重
- [x] **portfolio.py 统一 FT API**

### P2 — 高级策略优化

- [x] **价格异动事件驱动** — 30s 轮询检测 5min/15min 大幅波动 → 紧急复审
- [x] **AI 决策质量追踪** — confidence vs 实际结果校准，绩效摘要注入 prompt

### P3 — 架构增强

- [x] **独立 K 线数据源** — Binance API fallback，不依赖 Freqtrade feather
- [x] **工作流容错** — 数据质量评分 + screen 过滤低质量币种 + 全局错误阈值
- [x] **回测评估框架** — 信号回测(胜率/盈亏比/连胜连败) + K 线复盘(MFE/MAE)
- [x] **多策略切换** — 趋势/震荡/高波动市场状态检测 + 差异化参数

### P4 — 实战优化与可观测性

- [x] **graph.py 拆分重构** — 1295 行单文件 → 8 个独立模块 (每个 50-250 行)
- [x] **AI 角色历史准确率反馈** — `analyst_votes` 记录 + `calc_analyst_accuracy()` 统计
- [x] **配置热更新** — settings.yaml mtime 检测，变化时自动 reschedule
- [x] **Web Dashboard** — FastAPI + HTMX，自动刷新面板
- [x] **WebSocket 替代轮询** — Binance miniTicker 实时推送，REST fallback
- [x] **Prompt A/B 测试框架** — 按 prompt_version 分组对比绩效

### P5 — 部署上线与验证

- [x] **10.1 Docker Compose 一键部署** — `docker compose up -d` 启动全部服务
- [x] **10.2 启动健康检查 `cryptobot doctor`** — 12 项环境检查
- [x] **10.3 环境初始化 `cryptobot init`** — 引导式首次配置
- [x] **10.4 Freqtrade dry_run 自动同步** — journal sync 定时任务 (每 30min)
- [x] **10.5 每日绩效报告** — UTC 0:05 自动推送 Telegram 日报
- [x] **10.6 Dashboard K 线图 + 交易历史** — lightweight-charts K 线 + 信号标记 + 交易表格
- [x] **10.7 结构化日志** — JSON 格式日志输出 + 文件轮转
- [x] **10.8 Prompt 迭代工具链** — 版本管理 + CLI 命令 (list/new-version/activate/show)
- [x] **10.9 动态置信度阈值** — 基于历史校准自动调整 min_confidence

### P6 — 自我进化

- [x] **11.1 分析师动态权重** — 准确率 > 70% 升权 / < 45% 降权，自动标注
- [x] **11.2 分析师级模型选择** — `role_models` 按角色指定不同模型
- [x] **11.3 Prompt 集按市场状态切换** — 趋势/震荡/高波动市注入不同偏好 addon
- [x] **11.4 市场状态转换平滑** — 连续 N 周期确认才切换 regime，防止边界跳动
- [x] **11.5 绩效驱动自动 Prompt 迭代** — 退化检测 → 失败分析 → AI 改进 → 新版本
- [x] **11.6 多模型竞赛** — 并行调用多模型，consensus/best_performer 策略择优
- [x] **11.7 策略顾问 Agent** — 绩效驱动策略规则自动生成，14 天评估 + 续期/淘汰闭环

### P7 — 数据源增强

- [x] **12.1 宏观经济日历** — FinnHub FOMC/CPI/NFP，高影响事件前降杠杆
- [x] **12.2 清算热力图** — CoinGlass 清算聚集区，补充关键价位
- [x] **12.3 稳定币流入流出** — DefiLlama USDT/USDC 铸造/销毁净额
- [x] **12.4 期权市场数据** — Deribit Put/Call 比率 + Max Pain + IV
- [x] **12.5 订单簿深度** — Binance bid/ask 不平衡，大额挂单墙
- [x] **12.6 交易所储备量** — CoinGlass BTC/ETH 储备变化趋势
- [x] **12.7 代币稀释风险** — CoinGecko 供应量/稀释评估
- [x] **12.8 DXY 美元指数** — Yahoo Finance DXY 走势，宏观关联
- [x] **12.9 巨鲸钱包追踪** — Whale Alert 大额转账监控
- [x] **12.10 DeFi TVL 趋势** — DefiLlama 链级 TVL 健康度

### P8 — 量化验证 (部分完成)

- [x] **8.1 历史信号回测引擎** — 成本模型(手续费+滑点+资金费率) + 逐根K线模拟 + 净值曲线 + Sharpe/Sortino/MaxDD/Calmar
- [x] **8.3 随机基线对照** — 保持相同数量/方向/币种/杠杆分布，随机化入场时机
- [x] **8.4 简单策略基线** — MA 交叉(EMA 7/25)、RSI(30/70)、布林通道(20,2σ) + Welch's t-test 统计检验
- [x] **8.6 历史回放引擎** — 纯技术面 + 单次 LLM 调用，90天×5币种批量生成 197 信号，分页 K 线下载 + 断点续跑

### P10 — 统计严谨性 (部分完成)

- [x] **10.1 提高样本门槛** — Kelly 冷启动 10→50，币种/方向级 5→15，策略顾问 10→50/14→30天，Prompt 优化 10→30，置信度 30→50/8→15

---

## 六、当前系统状态

### 6.1 代码统计

| 指标 | 值 |
|------|-----|
| Python 源文件 | 100 个 |
| 代码行数 | ~16,000 行 |
| 测试文件 | 52 个 |
| 测试用例 | 914 passed |
| Lint | All checks passed (Ruff) |
| CLI 子命令 | 16 个命令组 |
| AI 角色 | 8 个 (4 haiku + 4 sonnet) |
| 数据源 | 16 个 |
| 监控交易对 | 10 个 |
| 定时任务 | 10 个 |
| API 端点 | 8 个 |

### 6.2 路线图完成度

| 阶段 | 状态 | 项目数 |
|------|------|--------|
| P0 资金安全 | **全部完成** | 4/4 |
| P1 运营闭环 | **全部完成** | 4/4 |
| P2 高级策略 | **全部完成** | 2/2 |
| P3 架构增强 | **全部完成** | 4/4 |
| P4 实战优化 | **全部完成** | 6/6 |
| P5 部署验证 | **全部完成** | 9/9 |
| P6 自我进化 | **全部完成** | 7/7 |
| P7 数据增强 | **全部完成** | 10/10 |
| P8 量化验证 | 部分完成 | 4/5 |
| P10 统计严谨 | 部分完成 | 1/5 |
| **合计** | | **51/56** |

### 6.3 部署文件

```
deploy/
├── preflight.sh                 # 部署前检查脚本
├── com.cryptobot.daemon.plist   # macOS launchd 开机自启
├── cryptobot-daemon.service     # Linux systemd 服务
└── logrotate-cryptobot.conf     # 日志轮转配置
.env.example                     # 环境变量模板
```

---

## 七、快速开始

```bash
# 1. 安装依赖
uv sync

# 2. 配置环境变量
cp .env.example .env
# 编辑 .env 填入 API key

# 3. 运行测试验证
uv run pytest tests/ -q

# 4. 环境健康检查
uv run cryptobot doctor

# 5. 手动运行一次分析 (dry-run)
uv run cryptobot workflow run --json-output

# 6. 启动 Freqtrade dry_run
freqtrade trade -c config/freqtrade/config_dry_run.json -s AgentSignalStrategy

# 7. 启动 daemon (自动调度)
uv run cryptobot daemon start --run-now

# 8. 启动 Web Dashboard (可选)
uv run cryptobot web start --port 8000

# 9. 查看绩效
uv run cryptobot journal stats

# 10. 管理 Prompt 版本
uv run cryptobot prompt list
uv run cryptobot prompt show
```

---

## 八、下一阶段路线图 — 从"AI 辅助"到"量化智能"

> **核心问题**: 当前系统工程完善但缺乏经过统计验证的交易优势 (edge)。所有交易决策依赖 LLM 语言推理，而非数学模型。以下路线图旨在补齐量化验证 → ML 建模 → 统计严谨性三块短板。

### P8 — 量化验证：回答"系统有没有 edge"

> **优先级: 最高** — 不知道有没有 edge 就上实盘，等于赌博。

| ID | 任务 | 说明 | 复杂度 |
|----|------|------|--------|
| 8.1 | ~~**历史信号回测引擎**~~ | ✅ 成本模型 + 逐根K线模拟 + 净值曲线 + Sharpe/Sortino/MaxDD/Calmar | 高 |
| 8.2 | **Walk-forward 验证** | 滚动窗口: 用 N 天训练（观察）→ M 天测试（交易），避免 look-ahead bias。至少 3 个完整周期 | 高 |
| 8.3 | ~~**随机基线对照**~~ | ✅ 保持相同数量/方向/币种/杠杆分布的随机信号 + Welch's t-test | 中 |
| 8.4 | ~~**简单策略基线**~~ | ✅ MA 交叉(EMA 7/25)、RSI(30/70)、布林通道(20,2σ) 三种基线 | 中 |
| 8.5 | **Edge 仪表盘** | CLI + Dashboard 展示: 累计净值曲线、夏普比率、Calmar 比率、每月收益热力图，实时回答"系统赚钱了吗" | 中 |
| 8.6 | ~~**历史回放引擎**~~ | ✅ 纯技术面 + 单次 LLM 调用，90天×5币 → 197 信号，分页K线下载 + 断点续跑 | 高 |

**验收标准**: 系统在 3 个月样本外数据上，扣除所有成本后，夏普比率 > 1.0 且显著优于随机基线 (p < 0.05)。

**90 天回放结果** (2026-02-22):

| 策略 | 笔数 | 胜率 | Sharpe | 最大回撤 | 总收益 |
|------|------|------|--------|----------|--------|
| **AI Replay** | **197** | **49.8%** | **+7.06** | **74.0%** | **+2863.9%** |
| MA 交叉 | 112 | 35.7% | -10.99 | 99.0% | -96.9% |
| RSI | 68 | 50.0% | -2.29 | 82.3% | -56.8% |
| 布林通道 | 150 | 37.3% | -12.62 | 99.5% | -98.4% |

### P9 — ML 模型引入：让数学做预测，LLM 做综合

> **核心思路**: LLM 擅长理解和综合文本信息，但不擅长从数值数据中发现统计规律。引入 ML 模型处理结构化数据，LLM 负责非结构化信息综合和最终决策解释。

| ID | 任务 | 说明 | 复杂度 |
|----|------|------|--------|
| 9.1 | **特征工程管道** | 从现有 16 个数据源提取数值特征（技术指标、链上指标、情绪分数等），标准化后存入特征矩阵。目标: 每个币种每个时间点 50-100 维特征 | 高 |
| 9.2 | **信号评分模型** | 训练 LightGBM/XGBoost 分类器: 输入特征 → 输出"未来 N 小时涨跌概率"。用历史 K 线标注，5-fold 交叉验证 | 高 |
| 9.3 | **ML 信号过滤层** | 在 trade 节点之后、risk_review 之前加入 ML 过滤: 如果 ML 模型预测方向与 LLM 决策一致且概率 > 0.6 才放行，否则降级或拒绝 | 中 |
| 9.4 | **特征重要性反馈** | 将 ML 模型的 feature importance 排名注入 analyst prompt，引导分析师关注真正有预测力的指标 | 低 |
| 9.5 | **模型定期重训** | 每周/每月用最新数据增量训练，保存模型版本，自动回退到表现更好的版本 | 中 |

**架构变化**: LLM 从"独立决策者"变为"综合判官"，ML 模型提供量化信号，LLM 负责综合非结构化信息（新闻、宏观事件）做最终裁定。

### P10 — 统计严谨性：让自优化闭环真正可靠

> **当前问题**: 策略顾问/prompt 优化器/置信度校准器都在用极小样本 (10-15 笔) 做决策，统计上几乎无意义。

| ID | 任务 | 说明 | 复杂度 |
|----|------|------|--------|
| 10.1 | ~~**提高样本门槛**~~ | ✅ Kelly 10→50，币种/方向 5→15，策略顾问 10→50/14→30天，Prompt 优化 10→30，置信度 30→50/8→15 | 低 |
| 10.2 | **A/B 测试增强** | 新规则/新 prompt 只对随机 50% 币种生效，另 50% 作为对照组。14 天后用双样本 t 检验判断差异是否显著 | 中 |
| 10.3 | **置信区间替代点估计** | 所有绩效指标附带 95% 置信区间（bootstrap）。"胜率 40% ± 15%" 和 "胜率 40% ± 3%" 含义完全不同 | 中 |
| 10.4 | **过拟合检测** | 监控"策略修改频率"和"修改后绩效波动"。如果系统频繁修改策略但绩效不改善，自动降低修改频率 | 中 |
| 10.5 | **Regime 感知评估** | 规则有效性评估时控制市场状态变量: 只在相同 regime 下对比前后绩效，排除环境变化的干扰 | 中 |

### P11 — 高级量化能力

> **前提**: P8-P10 验证通过后再推进。

| ID | 任务 | 说明 | 复杂度 |
|----|------|------|--------|
| 11.8 | **多因子相关性分析** | 计算各数据源与价格变动的 lead-lag 相关性，自动识别哪些因子有预测力、最佳领先时间 | 高 |
| 11.9 | **动态仓位优化** | 基于 Kelly 公式 + 历史胜率/盈亏比的实时更新，自动调整仓位比例。加入序列相关性修正 | 中 |
| 11.10 | **跨币种相关性风控** | 实时监控持仓间相关性，高相关性持仓视为同一方向暴露，自动降低总仓位 | 中 |
| 11.11 | **Regime 概率模型** | 用 HMM (Hidden Markov Model) 替代规则式 regime 检测，输出各状态概率而非硬分类 | 高 |
| 11.12 | **交易成本优化** | 分析不同时段的滑点和流动性，选择最优执行时间；考虑资金费率周期选择开仓时机 | 中 |

### P12 — 回放验证后优化：从"有 edge"到"可实盘"

> **前提**: 90 天回放已验证 AI 信号显著优于基线 (Sharpe 7.06 vs 全负)。但存在方向偏差、高回撤等问题，需优化后才可上实盘。

| ID | 任务 | 说明 | 复杂度 | 优先级 |
|----|------|------|--------|--------|
| 12.1 | **置信度分层分析** | 从 197 信号中按 55-65/65-75/75+ 分层，分析各区间胜率/盈亏比/Sharpe，找到最优阈值 | 低 | 短期 |
| 12.2 | **方向偏差诊断** | 184/197 为 short — 分析是 Prompt 系统性做空倾向还是纯市场趋势驱动。牛市回放验证 | 中 | 短期 |
| 12.3 | **回撤优化** | 74% 最大回撤不可接受。引入仓位上限/动态杠杆/每日亏损熔断，目标 MaxDD < 30% | 中 | 短期 |
| 12.4 | **多周期回放验证** | 跑 180 天/365 天覆盖牛市+震荡市，验证非仅熊市有效。不同 regime 分别统计 | 中 | 中期 |
| 12.5 | **启动 VPS daemon 积累实盘信号** | 跑真实 AI 工作流 (含链上/情绪/新闻)，积累 30 天+ 归档信号 | 低 | 中期 |
| 12.6 | **实盘信号 vs 回放信号对比** | daemon 积累足够信号后，用 `backtest compare` 对比全数据源 AI vs 纯技术面回放 | 中 | 中期 |
| 12.7 | **按币种差异化策略** | XRP 60% 胜率 +3.9%/笔 vs BNB 40% +0.8% — 考虑按币种调整杠杆/仓位/过滤 | 中 | 长期 |

**短期验证清单** (无需新代码，直接分析已有数据):
1. 下载 VPS 回放报告 → 按置信度分桶统计
2. 按日期分组 → 检查是否存在"爆发期"偏差 (少数交易日贡献大部分收益)
3. 分析 short-only 假设 → 如果只保留 short 信号，Sharpe 是否更高

### 推荐实施顺序

```
P12 回放验证后优化 (当前重点)
├── 12.1 置信度分层分析    ← 立即可做，分析已有数据
├── 12.2 方向偏差诊断      ← 与 12.1 同步
├── 12.3 回撤优化          ← 短期关键，MaxDD 74% → <30%
├── 12.4 多周期回放验证    ← 验证鲁棒性
├── 12.5 启动 VPS daemon   ← 积累真实信号
└── 12.6 实盘 vs 回放对比  ← 12.5 积累 30 天后

P8 量化验证 (继续推进)
├── 8.2 Walk-forward       ← 12.4 多周期数据可用后
└── 8.5 Edge 仪表盘        ← 可视化验证结果

P10 统计严谨性 (与 P12 并行)
├── 10.3 置信区间          ← 让指标更诚实
├── 10.2 A/B 测试增强      ← 依赖足够样本
└── 10.5 Regime 感知评估    ← 12.2 方向偏差分析后

P9 ML 模型引入 (P8/P12 验证后)
├── 9.1 特征工程           ← 数据基础
├── 9.2 信号评分模型       ← 核心 ML
├── 9.3 ML 信号过滤层      ← 集成
└── 9.5 模型定期重训       ← 维护

P11 高级量化 (P9 稳定后)
└── 按需选择
```

---

## 九、风险与注意事项

### API 配额

| API | 用量/轮 | 限制 | 风险 |
|-----|---------|------|------|
| Claude CLI (Max 5x) | ~40 次/2h | 225 次/5h | 低 (~18%) |
| DeepSeek API | ~40 次/2h | 高并发 | 极低 |
| CoinGlass | ~10 次/2h | 取决于套餐 | 中 (偶尔 429) |
| CoinGecko (免费) | ~5 次/2h | 10-30 次/min | 低 |
| CryptoNews-API | ~5 次/2h | 取决于套餐 | 低 |
| Binance (公开) | ~12 次/2h | 1200 次/min | 极低 |
| FinnHub | ~2 次/2h | 60 次/min | 极低 |
| Deribit (公开) | ~5 次/2h | 无明确限制 | 极低 |
| DefiLlama (公开) | ~5 次/2h | 无明确限制 | 极低 |

### 关键约定

- Python 3.12，Ruff line-length=100
- 包管理用 uv（hatchling 构建）
- 信号文件原子写入：先写 `.json.tmp` 再 rename
- 交易对格式：代码中 `BTCUSDT`，Freqtrade 中 `BTC/USDT:USDT`
- 网络请求测试全部 mock，标记 `@pytest.mark.network` 需真实网络
- Evolution 数据存储在 `data/output/evolution/` 目录
