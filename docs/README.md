# CryptoBot — 加密货币永续合约 AI 量化交易系统

> 版本: 2026-02-20 | 376 tests passed | 8,352 LOC | Lint clean

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

Web Dashboard (FastAPI + HTMX)
────────────────────────────────
持仓/信号/绩效 实时面板 ← /api/* JSON API ← 聚合所有数据源
```

### 1.2 架构图

```
┌─────────────────────────────────────────────────────────────────────────┐
│                        AI 分析工作流 (LangGraph 7 节点)                   │
│                                                                         │
│  collect_data ──→ screen ──→ analyze (4分析师×5币=20 haiku)              │
│      │                           │                                      │
│  10币种数据采集              research (2研究员×5币=10 sonnet)              │
│  技术指标+链上+情绪+新闻         │                                      │
│  市场状态检测                 trade (5 sonnet) ← 持仓上下文+绩效反馈      │
│                                  │                                      │
│                            risk_review (5 sonnet) ← 硬性风控规则         │
│                                  │                                      │
│                              execute ──→ signal.json / pending          │
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
   ┌──────────────────┐     ┌──────────────────┐
   │  交易日志 Journal │     │  Telegram 通知    │
   │  信号全生命周期    │     │  信号/告警/日报    │
   │  绩效/胜率/校准    │     │  爆仓预警/异常     │
   └──────────────────┘     └──────────────────┘
```

### 1.3 LLM 调用方式

通过 `claude -p` 子进程调用 Claude Code 订阅额度（`workflow/llm.py`），**不需要 Anthropic API key**。每轮约 40 次调用：

| 角色 | 模型 | 数量 | 说明 |
|------|------|------|------|
| 4 分析师 | haiku | ×5 币 = 20 | 技术/链上/情绪/基本面 |
| 2 研究员 | sonnet | ×5 币 = 10 | 看多/看空论证 |
| 交易员 | sonnet | ×5 = 5 | 综合决策 |
| 风控经理 | sonnet | ×5 = 5 | 审核+硬性规则 |

---

## 二、模块职责

### 2.1 核心模块

| 模块 | 文件 | 职责 |
|------|------|------|
| **AI 工作流** | `workflow/graph.py` + `nodes/*.py` | LangGraph 7 节点状态图，拆分为 8 个独立模块 |
| **持仓复审** | `workflow/re_review.py` | 独立 `re_review()` 流程，AI 重新评估持仓并调整止损 |
| **工作流辅助** | `workflow/utils.py` + `state.py` | 公共数据获取函数 + WorkflowState 类型定义 |
| **LLM 封装** | `workflow/llm.py` | Claude CLI 子进程调用，内置速率限制和重试 |
| **角色 Prompt** | `workflow/prompts.py` | 8 个 AI 角色 system prompt + JSON schema + PROMPT_VERSION |
| **信号管理** | `signal/bridge.py` | signal.json / pending 原子写入校验 + `update_signal_field` 动态更新 |
| **实时监控** | `realtime/monitor.py` | 等待入场区间 + 5m 指标确认后 promote 信号 |
| **WebSocket 价格流** | `realtime/ws_price_feed.py` | Binance miniTicker 实时推送，线程安全缓存，自动重连 |
| **价格异动事件** | `events/price_monitor.py` | 30s 轮询检测 5min/15min 大幅波动 → 紧急复审 + 通知 |
| **事件分发** | `events/dispatcher.py` | 事件过滤、通知、触发复审 |
| **技术指标** | `indicators/calculator.py` | TA-Lib 全指标计算 + K 线数据加载（feather 优先 + Binance API fallback） |
| **多时间框架** | `indicators/multi_timeframe.py` | 1h/4h/1d 共振分析 + 量价分析 + 支撑阻力 |
| **加密特有指标** | `indicators/crypto_specific.py` | 资金费率/OI/多空比/清算数据整合 |
| **市场结构** | `indicators/market_structure.py` | 结构分析 |

### 2.2 数据模块

| 模块 | 文件 | 数据源 |
|------|------|--------|
| **链上数据** | `data/onchain.py` | CoinGlass 资金费率/OI/多空比 |
| **情绪数据** | `data/sentiment.py` | Fear&Greed Index |
| **新闻数据** | `data/news.py` + `crypto_news.py` | CryptoNews-API + CoinGecko |
| **清算数据** | `data/liquidation.py` | CoinGlass 清算热力图 |

### 2.3 交易与风控

| 模块 | 文件 | 职责 |
|------|------|------|
| **仓位计算** | `risk/position_sizer.py` | Kelly + 固定风险法，已接入工作流 |
| **爆仓距离** | `risk/liquidation_calc.py` | 爆仓价格计算 + 预警分级 |
| **Freqtrade API** | `freqtrade_api.py` | 统一 FT REST API 封装 |
| **Freqtrade 策略** | `AgentSignalStrategy.py` | 4 级动态止损 + 分批止盈 + 仓位控制 + Agent 尾随 |

### 2.4 运营模块

| 模块 | 文件 | 职责 |
|------|------|------|
| **交易日志** | `journal/models.py` + `storage.py` | SignalRecord 全生命周期（pending → active → closed） |
| **绩效分析** | `journal/analytics.py` | 胜率/盈亏比/置信度校准/分析师准确率/按币种统计 |
| **回测评估** | `backtest/evaluator.py` | 信号回测：胜率/盈亏比/连胜连败 + K 线复盘(MFE/MAE) |
| **A/B 测试** | `backtest/ab_test.py` | 按 prompt_version 分组对比绩效 |
| **通知系统** | `notify.py` | Telegram 推送：信号/风控/告警/错误（silent fallback） |
| **调度器** | `cli/scheduler.py` | APScheduler: 5 个定时任务 + 配置热更新(2min) + WS/监控线程 |
| **Web Dashboard** | `web/app.py` + `routes/*.py` | FastAPI + HTMX：持仓/信号/绩效面板，6 个 API 端点 |
| **缓存** | `cache.py` | 文件级缓存，TTL 控制 |

### 2.5 CLI 命令 (12 个子命令组)

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
cryptobot web start [--port 8000]          # Web Dashboard
```

---

## 三、数据文件路径

| 文件 | 路径 | 说明 |
|------|------|------|
| 全局配置 | `config/settings.yaml` | 风控/调度/通知/事件等参数 |
| 交易对配置 | `config/pairs.yaml` | 10 币种 + 杠杆范围 + 相关性分组 |
| Freqtrade 配置 | `config/freqtrade/config.json` | 实盘配置 |
| Freqtrade dry_run | `config/freqtrade/config_dry_run.json` | 模拟盘配置 (10000 USDT) |
| 信号输出 | `data/output/signals/signal.json` | 活跃信号 |
| 待入场信号 | `data/output/signals/pending_signals.json` | 等待入场的信号 |
| 交易日志 | `data/output/journal/records.json` | 全部交易记录 |
| 缓存目录 | `data/output/.cache/` | API 响应缓存 |
| K 线数据 | `user_data/data/binance/futures/` | Freqtrade feather 文件 |
| 环境变量模板 | `.env.example` | API key 配置模板 |

---

## 四、配置说明

### 4.1 环境变量

```bash
# 必需
BINANCE_API_KEY=xxx         # Binance API (交易 + 数据)
BINANCE_API_SECRET=xxx

# 可选 (数据增强)
COINGLASS_API_KEY=xxx       # 链上/衍生品数据
CRYPTONEWS_API_KEY=xxx      # 新闻数据
COINGECKO_DEMO_KEY=xxx      # CoinGecko 数据

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

### 4.3 配置热更新

修改 `settings.yaml` 后无需重启 daemon。调度器每 2 分钟检查文件变化，自动重新加载调度间隔。

---

## 五、已完成路线图 (P0-P4)

### P0 — 资金安全 & 基础自动化

- [x] **仓位计算接入工作流** — `calc_position_size()` 替代硬编码 1000 USDT
- [x] **AI 持仓+账户上下文** — TRADER/RISK_MANAGER prompt 注入余额、持仓、风控规则
- [x] **硬性风控规则** — 代码层面 enforce `max_same_direction_pct` 等限制
- [x] **APScheduler 调度器** — `cryptobot daemon start` 一键启动 5 个定时任务

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
- [x] **Web Dashboard** — FastAPI + HTMX，6 个 API 端点，自动刷新面板
- [x] **WebSocket 替代轮询** — Binance miniTicker 实时推送，REST fallback
- [x] **Prompt A/B 测试框架** — 按 prompt_version 分组对比绩效

---

## 六、P5 路线图 — 部署上线与验证

> 系统功能已齐全，当前瓶颈是**跑起来并验证盈利能力**。

### 第一优先: 部署基础设施

#### 10.1 Docker Compose 一键部署

**目标:** `docker compose up -d` 启动全部服务。

```yaml
services:
  cryptobot:      # AI 分析 daemon + 事件监控
  freqtrade:      # 交易执行 (dry_run / live)
  dashboard:      # Web Dashboard (:8000)
```

**要点:**
- 多阶段构建，TA-Lib 预编译
- Volume 挂载: `config/`, `data/output/`, `user_data/`
- 环境变量通过 `.env` 文件注入
- 健康检查: Freqtrade API ping + CryptoBot scheduler 心跳

#### 10.2 启动健康检查 `cryptobot doctor`

新增 CLI 命令，逐项检查所有前置条件:

```bash
$ cryptobot doctor

[OK] Python 3.12.x
[OK] TA-Lib 已安装
[OK] Claude CLI 可用 (claude --version)
[OK] config/settings.yaml 存在
[OK] config/pairs.yaml 存在 (10 币种)
[OK] BINANCE_API_KEY 已设置
[OK] Binance API 连通 (ping)
[WARN] COINGLASS_API_KEY 未设置 (链上数据不可用)
[WARN] TELEGRAM_BOT_TOKEN 未设置 (通知不可用)
[OK] Freqtrade API 连通 (127.0.0.1:8080)
[OK] 数据目录可写 (data/output/)
```

#### 10.3 环境初始化 `cryptobot init`

引导式首次配置:

```bash
$ cryptobot init

创建目录结构... done
生成 .env 模板... done
拷贝默认 settings.yaml... done
请输入 Binance API Key: ***
请输入 Binance API Secret: ***
是否启用 Telegram 通知? [y/N]: y
请输入 Bot Token: ***
请输入 Chat ID: ***
写入 .env... done
运行 cryptobot doctor 验证... all OK
```

### 第二优先: Paper Trading 验证

#### 10.4 Freqtrade dry_run 自动同步增强

当前 `journal sync` 需手动运行。改进:

- 调度器新增 sync 定时任务 (每 1h 自动同步)
- 同步时自动匹配 signal_id → Freqtrade trade_id
- 计算实际 entry_price、exit_price、duration、PnL
- 更新 SignalRecord status: active → closed

#### 10.5 每日绩效报告

每天 UTC 0:00 自动生成日报推送 Telegram:

```
📊 CryptoBot 日报 (2026-02-20)

今日交易: 3 笔 (2胜 1负)
今日盈亏: +2.3% (+230 USDT)
当前持仓: 2 个 (BTCUSDT LONG +1.5%, ETHUSDT SHORT -0.3%)

近 7 天: 胜率 65%, 盈亏比 1.9:1
分析师最佳: 技术分析师 (准确率 72%)
分析师最差: 情绪分析师 (准确率 48%)
```

### 第三优先: 可观测性增强

#### 10.6 Dashboard K 线图 + 交易历史

当前 Dashboard 只有文字面板。增强:

- TradingView lightweight-charts 组件显示 K 线
- 入场/出场标记点叠加在 K 线上
- 持仓 P&L 实时曲线
- 交易历史时间线 (最近 30 天)

#### 10.7 结构化日志

当前日志是纯文本 `logging.info()`。改进:

- JSON 格式日志输出 (structlog / python-json-logger)
- 日志文件轮转 (每天 / 最多 7 天)
- 关键字段: timestamp, level, module, symbol, action, signal_id
- 便于后续接入 ELK / Grafana Loki

### 第四优先: 信号质量优化

#### 10.8 Prompt 迭代工具链

基于已有 A/B 框架，提供便捷的 prompt 迭代流程:

```bash
# 创建新版本 (自动复制当前 prompt + 递增版本号)
cryptobot prompt new-version --note "加强止损逻辑"

# 切换活跃版本
cryptobot prompt activate v1.1

# 对比版本绩效 (需要足够已平仓记录)
cryptobot backtest ab-test --days 30
```

#### 10.9 动态置信度阈值

根据历史数据自动调整 `min_confidence`:

- 统计各置信度区间的实际胜率
- 如果 confidence=60 的信号历史胜率 < 40%，自动提高阈值到 65
- 低准确率分析师在 trade prompt 中降权标注

---

## P6 路线图 — 自我进化

> 基于 P5 积累的交易数据，实现系统自动学习和优化，逐步减少人工干预。

### 第一层: 分析师级进化

#### 11.1 分析师动态权重

当前分析师准确率已有追踪（`calc_analyst_accuracy()`），但仅注入 prompt 供参考。改进为自动化闭环:

- 每日统计各分析师近 30 天准确率
- 准确率 < 45% 的分析师自动降权（trade prompt 中标注"近期准确率偏低，仅供参考"）
- 准确率 > 70% 的分析师自动升权（trade prompt 中标注"近期表现优异，重点参考"）
- 权重变化记录到 `data/output/evolution/weights.json`

#### 11.2 分析师级模型选择

利用 LLM 抽象层，为不同角色分配最优模型:

```yaml
# config/settings.yaml
llm:
  role_models:                        # 按角色指定模型 (可选，覆盖默认)
    technical_analyst: "deepseek-chat"
    sentiment_analyst: "deepseek-chat"
    bull_researcher: "deepseek-reasoner"
    bear_researcher: "deepseek-reasoner"
    trader: "deepseek-reasoner"
    risk_reviewer: "deepseek-reasoner"
```

- `call_claude()` 新增 `role` 参数，根据 `role_models` 映射实际模型
- 默认不配置时走全局 `models.haiku/sonnet` 映射（向后兼容）
- 支持为同一角色配置不同厂商（如技术分析用 DeepSeek，研究辩论用 Claude）

### 第二层: 市场状态自适应

#### 11.3 Prompt 集按市场状态切换

当前 `market_regime` 仅影响杠杆和置信度阈值。扩展为 prompt 集切换:

```yaml
# config/settings.yaml
market_regime:
  trending:
    prompt_version: "v1.0-trend"      # 趋势市专用 prompt
    min_confidence: 55
    max_leverage: 5
  ranging:
    prompt_version: "v1.0-range"      # 震荡市专用 prompt
    min_confidence: 65
    max_leverage: 3
  volatile:
    prompt_version: "v1.0-volatile"   # 高波动专用 prompt
    min_confidence: 70
    max_leverage: 2
```

- 趋势市 prompt 侧重突破、趋势跟踪、动量指标
- 震荡市 prompt 侧重支撑阻力、超买超卖、均值回归
- 高波动 prompt 侧重风控、缩小仓位、宽止损

#### 11.4 市场状态检测增强

当前市场状态检测在 `workflow/nodes/screen.py` 中。增强:

- 多时间框架状态聚合（4h + 1d + 1w）
- 状态转换平滑（连续 3 个周期确认后才切换，避免频繁跳转）
- 状态转换时自动通知（Telegram: "市场状态从 trending → volatile"）

### 第三层: 自动进化闭环

#### 11.5 绩效驱动的自动 Prompt 迭代

基于 10.8 的 Prompt A/B 框架，增加自动触发机制:

```
绩效下降检测 → 自动创建新 prompt 版本 → A/B 测试 → 择优上线
```

- 每日检查: 近 7 天胜率 < 近 30 天胜率 × 0.8 → 触发优化
- AI 自动分析失败案例，生成改进版 prompt（用 sonnet 分析 + 生成）
- 新 prompt 自动进入 A/B 测试（50% 流量）
- 10 笔交易后自动对比，胜率更高者全量上线
- 全过程记录到 `data/output/evolution/iterations.json`

#### 11.6 多模型竞赛

同时运行多个模型生成交易决策，择优执行:

- 每个分析周期，交易决策节点同时调用 2-3 个模型（如 DeepSeek + Qwen）
- 各模型独立输出信号，不互相参考
- 短期（< 50 笔）: 取共识信号（多数模型同意才执行）
- 长期（50+ 笔）: 按历史胜率加权，淘汰最差模型，引入新模型
- 竞赛结果记录到 `data/output/evolution/competition.json`

---

## P7 路线图 — 数据源增强

> 补充当前缺失的关键影响因子，提升 AI 决策的信息完备性。

### 高优先级

#### 12.1 宏观经济日历

FOMC 利率决议、CPI、非农就业等宏观事件前后市场波动剧烈。接入经济日历实现自动风控:

- 数据源: [ForexFactory](https://www.forexfactory.com/) 或 [Investing.com](https://www.investing.com/economic-calendar/) 公开日历
- 每日检查未来 24h 内的高影响力事件
- 高影响事件前 2h 自动降低杠杆上限（max_leverage × 0.5）
- 事件期间暂停新信号生成（可配置）
- 注入到情绪分析师和风控经理的 prompt 中: "注意: 今日 22:30 有 FOMC 利率决议"

```yaml
# config/settings.yaml
macro_calendar:
  enabled: true
  pre_event_hours: 2              # 事件前多久开始降杠杆
  pause_on_high_impact: true      # 高影响事件期间暂停交易
  data_source: "forexfactory"     # forexfactory / investing
```

#### 12.2 清算热力图

清算挂单聚集区是价格磁吸目标，对止盈止损设置极有参考价值:

- 数据源: CoinGlass Liquidation Heatmap API（已有 CoinGlass 接入）
- 获取上方/下方清算聚集价位和预估清算量
- 注入到技术分析师: 作为关键价位补充（与 Pivot/Fibonacci 并列）
- 注入到交易员: 止盈目标参考（"上方 $98,500 有 $2.3 亿清算挂单"）
- 新增 `data/liquidation_heatmap.py`

#### 12.3 稳定币流入流出

USDT/USDC 铸造量是资金入场的领先指标:

- 数据源: CoinGlass Stablecoin 数据 或 [DefiLlama](https://defillama.com/) Stablecoins API
- 追踪 24h/7d USDT 铸造/销毁净额
- 大额铸造（> $1 亿/天）= 资金入场信号 → 注入情绪分析师
- 持续销毁 = 资金离场 → 提高风控敏感度
- 新增 `data/stablecoin.py`

### 中优先级

#### 12.4 期权市场数据

机构通过期权表达方向性观点，Put/Call 比率和 Max Pain 是重要参考:

- 数据源: CoinGlass 或 Deribit 公开 API
- 关键指标:
  - Put/Call 比率: > 1.2 市场偏恐慌，< 0.7 市场偏乐观
  - Max Pain（最大痛点）: 期权到期日价格倾向收敛到此价位
  - 隐含波动率 (IV): IV 飙升预示大幅波动即将来临
- 注入到链上分析师 prompt 中
- 新增 `data/options.py`

#### 12.5 订单簿深度

大额挂单墙是短期支撑阻力的重要参考:

- 数据源: Binance Order Book API（depth endpoint，已有 Binance 接入）
- 计算 bid/ask 不平衡比: buy_wall / sell_wall
- 不平衡 > 2.0 = 强买盘支撑；< 0.5 = 强卖压
- 大额挂单价位注入到技术分析师的支撑阻力列表
- 新增 `data/orderbook.py`

#### 12.6 交易所储备量

交易所 BTC/ETH 存量变化反映潜在抛压:

- 数据源: CoinGlass Exchange Reserve 或 CryptoQuant
- 储备上升 = 潜在卖压增加（空头信号）
- 储备下降 = 囤币提走（多头信号）
- 7 天趋势比单日更有参考价值
- 注入到链上分析师 prompt 中
- 新增 `data/exchange_reserve.py`

#### 12.7 代币解锁日历

大额代币解锁前后容易引发抛压:

- 数据源: [Token Unlocks](https://token.unlocks.app/) 公开 API
- 检查监控币种未来 7 天内的解锁事件
- 解锁量 > 流通量 1% 标记为"重大解锁风险"
- 注入到基本面分析师: "注意: SUI 3 天后解锁 2% 流通量"
- 在 screen 节点中作为负面因子扣分
- 新增 `data/token_unlocks.py`

### 低优先级

#### 12.8 美元指数 (DXY) 关联

- 数据源: TradingView 或 Yahoo Finance API
- DXY 上涨通常利空加密货币，下跌利多
- 作为宏观背景注入情绪分析师（不单独作为交易信号）

#### 12.9 巨鲸钱包追踪

- 数据源: Whale Alert API 或 Arkham Intelligence
- 大额转入交易所 = 潜在抛售信号
- 数据噪声较大，仅作为辅助参考
- 注入到链上分析师

#### 12.10 DeFi TVL 趋势

- 数据源: DefiLlama API
- 特定链 TVL 持续流出 = 生态恶化（利空该链代币）
- 更适合中长期判断，对 2h 周期波段交易参考价值有限

---

## 七、实施优先级

| 编号 | 内容 | 复杂度 | 收益 | 前置条件 |
|------|------|--------|------|----------|
| **10.1** | Docker Compose | 中 | 可部署 | 无 |
| **10.2** | `cryptobot doctor` | 小 | 排查便利 | 无 |
| **10.3** | `cryptobot init` | 小 | 新用户体验 | 无 |
| **10.4** | 自动 journal sync | 小 | 数据积累 | Freqtrade dry_run 运行中 |
| **10.5** | 每日绩效报告 | 小 | 可观测 | 10.4 |
| **10.6** | Dashboard K 线图 | 中 | 可视化 | 无 |
| **10.7** | 结构化日志 | 小 | 运维 | 无 |
| **10.8** | Prompt 迭代工具 | 中 | 长期优化 | 50+ 已平仓记录 |
| **10.9** | 动态置信度阈值 | 中 | 信号质量 | 50+ 已平仓记录 |
| **11.1** | 分析师动态权重 | 小 | 信号质量 | 10.5 (准确率数据) |
| **11.2** | 分析师级模型选择 | 中 | 灵活性 | LLM 抽象层 (已完成) |
| **11.3** | Prompt 集市场状态切换 | 中 | 适应性 | 10.8 |
| **11.4** | 市场状态检测增强 | 小 | 稳定性 | 无 |
| **11.5** | 绩效驱动自动迭代 | 大 | 自动化 | 10.8 + 10.9 + 50+ 记录 |
| **11.6** | 多模型竞赛 | 大 | 信号质量 | 11.2 + 50+ 记录 |
| **12.1** | 宏观经济日历 | 中 | 风控 | 无 |
| **12.2** | 清算热力图 | 小 | 信号质量 | CoinGlass API (已接入) |
| **12.3** | 稳定币流入流出 | 小 | 资金面判断 | CoinGlass API (已接入) |
| **12.4** | 期权市场数据 | 中 | 机构预期 | 无 |
| **12.5** | 订单簿深度 | 小 | 短期支撑阻力 | Binance API (已接入) |
| **12.6** | 交易所储备量 | 小 | 抛压预判 | CoinGlass API (已接入) |
| **12.7** | 代币解锁日历 | 小 | 供应冲击 | 无 |
| **12.8** | DXY 美元指数 | 小 | 宏观参考 | 无 |
| **12.9** | 巨鲸钱包追踪 | 中 | 大资金动向 | 无 |
| **12.10** | DeFi TVL 趋势 | 小 | 生态健康 | 无 |

**建议顺序:** 10.1 → 10.2 → 10.3 → 启动 dry_run 积累数据 → 10.4 → 10.5 → 其余并行推进。P6 在积累 50+ 已平仓记录后启动: 11.1 → 11.2 → 11.4 → 11.3 → 11.5 → 11.6。P7 可随时启动，建议优先: 12.1 (防黑天鹅) → 12.2 + 12.3 (已有 API) → 12.5 (已有 API) → 其余按需。

---

## 八、当前系统状态

### 8.1 代码统计

| 指标 | 值 |
|------|-----|
| Python 源文件 | 66 个 |
| 代码行数 | 8,352 行 |
| 测试用例 | 376 passed |
| Lint | All checks passed (Ruff) |
| CLI 子命令 | 12 个命令组 |
| AI 角色 | 8 个 (4 haiku + 4 sonnet) |
| 监控交易对 | 10 个 |

### 8.2 模块覆盖率

| 模块 | 覆盖率 | 说明 |
|------|--------|------|
| indicators/ | 83-100% | 技术指标计算 |
| journal/ | 97-100% | 交易记录与绩效 |
| risk/ | 97-100% | 仓位计算与爆仓距离 |
| signal/ | 87% | 信号读写校验 |
| backtest/ | 高 | 回测评估 + A/B 测试 |
| workflow/ | 29% | LLM 集成层，依赖 mock |
| cli/ | 20-29% | 展示层，手动验证为主 |

### 8.3 部署文件

```
deploy/
├── preflight.sh                 # 部署前检查脚本
├── com.cryptobot.daemon.plist   # macOS launchd 开机自启
├── cryptobot-daemon.service     # Linux systemd 服务
└── logrotate-cryptobot.conf     # 日志轮转配置
.env.example                     # 环境变量模板
```

---

## 九、快速开始

```bash
# 1. 安装依赖
uv sync

# 2. 配置环境变量
cp .env.example .env
# 编辑 .env 填入 API key

# 3. 运行测试验证
uv run pytest tests/ -q

# 4. 手动运行一次分析 (dry-run)
uv run cryptobot workflow run --json-output

# 5. 启动 Freqtrade dry_run
freqtrade trade -c config/freqtrade/config_dry_run.json -s AgentSignalStrategy

# 6. 启动 daemon (自动调度)
uv run cryptobot daemon start --run-now

# 7. 启动 Web Dashboard (可选)
uv run cryptobot web start --port 8000

# 8. 查看绩效
uv run cryptobot journal stats
```

---

## 十、风险与注意事项

### API 配额

| API | 用量/轮 | 限制 | 风险 |
|-----|---------|------|------|
| Claude CLI (Max 5x) | ~40 次/2h | 225 次/5h | 低 (~18%) |
| CoinGlass | ~10 次/2h | 取决于套餐 | 中 (偶尔 429) |
| CoinGecko (免费) | ~5 次/2h | 10-30 次/min | 低 |
| CryptoNews-API | ~5 次/2h | 取决于套餐 | 低 |
| Binance (公开) | ~12 次/2h | 1200 次/min | 极低 |

### 关键约定

- Python 3.12，Ruff line-length=100
- 包管理用 uv（hatchling 构建）
- 信号文件原子写入：先写 `.json.tmp` 再 rename
- 交易对格式：代码中 `BTCUSDT`，Freqtrade 中 `BTC/USDT:USDT`
- 网络请求测试全部 mock，标记 `@pytest.mark.network` 需真实网络
