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

**建议顺序:** 10.1 → 10.2 → 10.3 → 启动 dry_run 积累数据 → 10.4 → 10.5 → 其余并行推进。

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
