# 加密货币永续合约 AI 量化交易系统 — 改进路线图

> 版本: 2026-02-20 | 基于代码审计 + 差距分析
>
> 目标: 实现 7×24 全自动运行，最大化风险调整后收益

---

## 一、系统现状

### 1.1 已完成功能

| 模块 | 状态 | 说明 |
|------|------|------|
| LangGraph 7 节点工作流 | 完成 | collect → screen → analyze → research → trade → risk_review → execute |
| 8 个 AI 角色 | 完成 | 4 分析师(haiku) + 2 研究员(sonnet) + 交易员(sonnet) + 风控经理(sonnet) |
| 技术指标计算 | 完成 | 4h K 线 TA-Lib 全指标 + 多时间框架共振 + 量价分析 + 支撑阻力 |
| 链上/衍生品数据 | 完成 | CoinGlass 资金费率/OI/多空比 + 清算数据 |
| 情绪/新闻数据 | 完成 | Fear&Greed + CryptoNews-API + CoinGecko |
| 信号管理 | 完成 | 原子写入 signal.json/pending_signals.json + 校验 + 过期清理 |
| 实时入场监控 | 完成 | 轮询 Binance (10s) + entry_range 判断 + 5m 指标确认 |
| Freqtrade 策略 | 完成 | 4 级动态止损 + 分批止盈 + 仓位控制 + Agent 尾随止损 |
| 持仓复审 (re-review) | 完成 | AI 重新评估持仓 + 动态调整止损 |
| CLI 7 个子命令 | 完成 | workflow/signal/monitor/portfolio/data/realtime |
| 代码质量优化 | 完成 | 10 项修复(132 测试全通过) |

### 1.2 架构图

```
                            ┌──────────────────────────────────────────────┐
                            │           AI 分析工作流 (每 2h)                │
                            │                                              │
                            │  collect_data ──→ screen ──→ analyze(×20)    │
                            │       │                        │              │
                            │       │                   research(×10)      │
                            │       │                        │              │
                            │       │                    trade(×5)          │
                            │       │                        │              │
                            │       │                 risk_review(×5)       │
                            │       │                        │              │
                            │       │                    execute            │
                            └───────┼────────────────────────┼──────────────┘
                                    │                        │
                         realtime.enabled?            realtime.enabled?
                          false ↓     true ↓          false ↓     true ↓
                                │           │               │           │
                     signal.json│   pending_signals.json    │           │
                                │           │               │           │
                                │    ┌──────┴──────┐        │           │
                                │    │ 实时入场监控  │        │           │
                                │    │ (10s 轮询)   │        │           │
                                │    │ 价格 + 5m 确认│        │           │
                                │    └──────┬──────┘        │           │
                                │           │               │           │
                                │    signal.json            │           │
                                │           │               │           │
                                └───────────┼───────────────┘           │
                                            │                           │
                                  ┌─────────┴─────────┐                 │
                                  │ Freqtrade (5m K线) │                 │
                                  │ AgentSignalStrategy │                │
                                  │  - custom_stoploss  │                │
                                  │  - custom_exit      │                │
                                  │  - adjust_position  │                │
                                  └─────────────────────┘
```

### 1.3 当前数据流 (每轮约 40 次 Claude 调用)

```
数据采集(纯 API)          AI 分析层                   执行层
─────────────           ─────────                   ─────
10 币种技术指标           4 分析师 × 5 币 = 20 haiku   signal.json
CoinGlass 衍生品         2 研究员 × 5 币 = 10 sonnet  Freqtrade 策略
Fear&Greed              5 交易决策 sonnet             交易所执行
CryptoNews              5 风控审核 sonnet
CoinGecko(延迟加载)
```

### 1.4 现状诊断

系统完成了核心信号链：**AI 分析 → 信号生成 → Freqtrade 执行**。但距离"完全自动化 + 高回报"还有几个关键断层：

**直接影响盈亏的问题：**
- 仓位计算断路 — AI 说"投 10% 资金"，系统无视它，每次开 1000U 固定仓位
- 无持仓上下文 — AI 不知道已有仓位，可能 5 个信号全做多，总敞口 100%
- 无调度器 — 手动漏掉一次 = 信号过期 = 错过行情
- 无通知 — 爆仓告警只打印到终端，cron 跑时看不到

**显著提升胜率的改进：**
- 无交易记录闭环 — AI 做决策是开环的，无法自我修正
- 无事件驱动 — BTC 5 分钟暴跌 5%，系统要等 2h 才能反应

**P0 不做的话系统跑起来也是赌博。**

---

## 二、关键差距分析

### 差距总览

| 优先级 | 差距 | 影响 | 复杂度 |
|--------|------|------|--------|
| **P0** | 仓位计算断路 | 每笔固定 1000 USDT，资金效率低，风控参数形同虚设 | 中 |
| **P0** | AI 无持仓/账户上下文 | 交易员不知道现有仓位/余额，可能重复开仓或超额 | 中 |
| **P0** | 无自动调度器 | 2h 工作流和 5min 监控需手动启动或外部 cron | 低 |
| **P1** | 无通知系统 | 重要事件(开仓/止损/异常)无法及时通知 | 低 |
| **P1** | 无交易记录与绩效闭环 | 无法评估 AI 历史表现，无法校准置信度 | 中 |
| **P1** | portfolio.py 重复代码 | 硬编码 FT API 地址/密码，与已修复的 monitor.py 不一致 | 低 |
| **P2** | 无事件驱动能力 | BTC 暴跌 10% 等极端行情只能等 2h 周期才反应 | 中 |
| **P2** | AI 决策质量追踪 | 无 AI 置信度 vs 实际结果校准，无法改进 prompt | 中 |
| **P3** | K 线数据依赖 Freqtrade | 技术指标依赖 Freqtrade feather 文件，独立运行受限 | 中 |
| **P3** | 工作流节点容错 | 单节点失败(如 CoinGlass 429)可能阻塞整条流水线 | 中 |
| **P3** | 回测验证 AI prompt | 无法用历史数据验证 prompt 改动是否提升信号质量 | 大 |
| **P3** | 多策略切换 | 趋势/震荡/高波动市场用同一套参数，适应性差 | 大 |

---

## 三、P0 — 资金安全 & 基础自动化

### 3.1 仓位计算接入工作流

#### 问题现状

`risk/position_sizer.py` 已实现完整的 Kelly + 固定风险仓位计算：

```python
def calc_position_size(symbol, account_balance, entry_price, stop_loss_price,
                       leverage, win_rate, avg_win_loss_ratio) -> dict:
    # 返回 margin_usdt, notional_usdt, max_loss_usdt 等
```

但工作流中 **完全未调用**。问题链条:

1. `_decision_to_signal()` (graph.py:714-732) 传递 `position_size_pct`（AI 建议的百分比）
2. `validate_signal()` (bridge.py:116) 硬编码 `position_size_usdt: 1000`
3. Freqtrade `custom_stake_amount()` 使用 `position_size_usdt` 作为实际仓位

**结果:** 无论 AI 建议什么仓位比例、无论账户余额多少，每笔交易固定 1000 USDT。

#### 解决方案

在 `risk_review` 节点（风控通过后）调用 `calc_position_size()` 计算精确仓位：

```
风控审核通过 → calc_position_size(symbol, balance, entry_price, stop_loss, leverage)
            → 写入 position_size_usdt 到信号
```

**需要的输入:**
- `account_balance`: 从 Freqtrade `/balance` API 获取 USDT 余额
- `entry_price`: 取 `entry_price_range` 中点
- `stop_loss_price`: 来自 AI 交易决策
- `leverage`: 来自 AI 交易决策

**文件变更:**

| 文件 | 变更 |
|------|------|
| `workflow/graph.py` | `_decision_to_signal()` 调用 `calc_position_size()` |
| `workflow/graph.py` | `risk_review()` 开头获取账户余额 |
| `signal/bridge.py` | `validate_signal()` 移除硬编码 1000 默认值 |

#### 预期效果

- 单笔最大亏损严格控制在账户 2% 以内
- Kelly 公式优化仓位大小（目前只有半 Kelly）
- `max_single_position_pct: 25%` 上限约束生效

---

### 3.2 AI 持仓 + 账户上下文

#### 问题现状

TRADER prompt (graph.py:588-601) 的输入:
```
当前价格: {current_price}
最大杠杆: {max_leverage}x
看多研究员观点: ...
看空研究员观点: ...
分析师数据: ...
```

**缺失信息:**
- 账户总余额和可用余额
- 当前持仓列表（币种、方向、仓位大小、盈亏）
- settings.yaml 中的风控规则:
  - `max_same_direction_pct: 50%` — 同方向持仓不超过总资金 50%
  - `max_same_category_pct: 40%` — 同类别（如 smart_contract）不超 40%
  - `max_correlated_same_direction: 3` — 高相关组同向上限 3 个
  - `max_total_position_pct: 80%` — 总持仓不超过 80%

RISK_MANAGER prompt 同样缺失这些信息。

#### 解决方案

**1. 在 `trade` 节点添加持仓上下文:**

```python
# graph.py trade() 中
from cryptobot.freqtrade_api import ft_api_get

positions = ft_api_get("/status") or []
balance = ft_api_get("/balance") or {}
usdt_balance = 0
for b in balance.get("currencies", []):
    if b.get("currency") == "USDT":
        usdt_balance = b.get("balance", 0)

# 构建持仓摘要
position_summary = []
for p in positions:
    position_summary.append({
        "pair": p.get("pair"),
        "direction": "SHORT" if p.get("is_short") else "LONG",
        "leverage": p.get("leverage"),
        "profit_pct": p.get("profit_pct"),
        "profit_abs": p.get("profit_abs"),
    })
```

**2. 传入 TRADER/RISK_MANAGER prompt:**

```
### 账户状态
USDT 余额: {usdt_balance}
已用: {used}
持仓 {len(positions)} 个:
{position_summary}

### 风控规则
- 同方向总仓位上限: 50%
- 同类别最大: 40%
- 总持仓上限: 80%
```

**3. 在 `risk_review` 节点 enforce 硬性规则:**

```python
# 硬性检查（不依赖 AI 判断）
total_used_pct = used / balance * 100
if total_used_pct > risk_cfg["max_total_position_pct"]:
    # 拒绝新开仓
    continue

# 同方向检查
same_dir = [p for p in positions if (p["is_short"] == (action == "short"))]
same_dir_pct = sum(...) / balance * 100
if same_dir_pct + new_position_pct > risk_cfg["max_same_direction_pct"]:
    continue
```

**文件变更:**

| 文件 | 变更 |
|------|------|
| `workflow/graph.py` | `trade()` 获取持仓/余额，传入 prompt |
| `workflow/graph.py` | `risk_review()` 添加硬性规则检查 |
| `workflow/prompts.py` | TRADER/RISK_MANAGER prompt 增加持仓上下文说明 |

#### 预期效果

- AI 交易员能感知已有持仓，避免在同一方向过度集中
- 风控经理有账户全局视图，审核更精准
- 硬性规则在代码层面强制执行，不依赖 AI 判断

---

### 3.3 内置调度器

#### 问题现状

当前 2h 分析周期、5min 监控检查、re-review 等全部需要:
- 用户手动运行 `uv run cryptobot workflow run`
- 或配置外部 cron（但 cron 无法管理进程状态/失败重试）

#### 解决方案

新增 `cli/scheduler.py` 命令，基于 APScheduler 实现:

```python
# uv run cryptobot scheduler start
@scheduler.command("start")
def start():
    """启动所有定时任务"""
    sched = BackgroundScheduler()

    # 每 2h 运行完整分析工作流
    sched.add_job(run_workflow, "interval", hours=2, id="full_cycle")

    # 每 5min 运行 check-alerts
    sched.add_job(run_check_alerts, "interval", minutes=5, id="check_alerts")

    # 每 4h 运行 re-review (持仓复审)
    sched.add_job(run_re_review, "interval", hours=4, id="re_review")

    # 每 24h 清理过期信号
    sched.add_job(cleanup_expired, "interval", hours=24, id="cleanup")

    sched.start()
    # 同时启动实时入场监控 (如果 enabled)
    if settings.realtime.enabled:
        start_realtime_monitor()
```

**文件变更:**

| 文件 | 变更 |
|------|------|
| `cli/scheduler.py` | **新建** — 调度器命令 |
| `cli/__init__.py` | 注册 scheduler 子命令 |
| `pyproject.toml` | 添加 `apscheduler` 依赖 |

#### 预期效果

- 一条命令 `uv run cryptobot scheduler start` 启动全部定时任务
- 进程内管理，支持失败重试和日志
- 替代外部 cron 依赖

---

## 四、P1 — 运营闭环

### 4.1 通知系统

#### 问题现状

Freqtrade 自身有 Telegram 通知（开仓/平仓），但 AI 工作流层面零通知:
- 工作流运行异常（如 Claude CLI 全部限流失败）
- 新信号生成
- re-review 调整止损
- 爆仓距离预警
- 连续亏损达到日/周限额

#### 解决方案

新增 `src/cryptobot/notify.py`，支持 Telegram:

```python
async def send_alert(level: str, title: str, body: str):
    """发送告警到 Telegram
    level: info / warning / critical
    """
    # 读取 settings.yaml 中的 telegram bot_token + chat_id
    # 格式化消息并发送
```

在关键节点调用:

| 触发点 | 级别 | 消息内容 |
|--------|------|----------|
| execute 写入新信号 | info | "新信号: LONG BTCUSDT 3x, 入场 94000-95000" |
| risk_review 拒绝 | info | "风控拒绝: ETHUSDT, 原因: ..." |
| re-review 调整止损 | warning | "止损调整: BTCUSDT 92000 → 93500" |
| 爆仓距离 < 20% | critical | "爆仓预警: BTCUSDT 距爆仓 18.5%" |
| 工作流错误 > 3 | warning | "工作流异常: 5 个错误" |
| 日亏损 > 5% | critical | "日亏损 -5.2%, 建议暂停交易" |

**文件变更:**

| 文件 | 变更 |
|------|------|
| `src/cryptobot/notify.py` | **新建** — 通知发送封装 |
| `config/settings.yaml` | 添加 `telegram.bot_token` / `chat_id` |
| `workflow/graph.py` | execute/risk_review 中调用 send_alert |
| `cli/workflow.py` | re-review 调用 send_alert |
| `cli/monitor.py` | check-alerts 调用 send_alert |

---

### 4.2 交易记录与绩效闭环

#### 问题现状

系统没有任何交易历史记录。无法回答:
- AI 历史胜率多少？
- confidence=80 的信号实际成功率多少？
- 哪个分析师角色最准确？
- 盈亏比是否达到预期？

Freqtrade 有自己的 SQLite 数据库 (`tradesv3.dryrun.sqlite`)，但:
1. 只记录 Freqtrade 层面的交易信息
2. 不包含 AI 分析过程（confidence、reasoning、分析师报告）
3. 不包含信号元信息（什么时候生成、什么时候入场、等待了多久）

#### 解决方案

新增 `src/cryptobot/journal/` 交易日志模块:

```
journal/
├── __init__.py
├── models.py        # 数据模型 (TradeRecord, SignalRecord)
├── storage.py       # JSON 文件存储 (data/output/journal/)
└── analytics.py     # 绩效分析计算
```

**核心数据模型:**

```python
@dataclass
class SignalRecord:
    signal_id: str              # UUID
    symbol: str
    action: str                 # long/short
    timestamp: str              # 信号生成时间
    confidence: int             # AI 置信度
    entry_price_range: list     # 预期入场区间
    stop_loss: float
    take_profit: list
    leverage: int
    position_size_usdt: float
    analysis_summary: dict      # 包含 reasoning, risk_score
    # 结果字段 (交易结束后填充)
    actual_entry_price: float | None = None
    actual_exit_price: float | None = None
    actual_pnl_pct: float | None = None
    actual_pnl_usdt: float | None = None
    exit_reason: str | None = None   # tp_hit / sl_hit / manual / expired
    duration_hours: float | None = None
    status: str = "pending"     # pending / active / closed / expired
```

**数据写入时机:**

| 事件 | 写入内容 |
|------|----------|
| execute 写入信号 | 创建 SignalRecord (status=pending) |
| 实时监控 promote | 更新 actual_entry_price, status=active |
| Freqtrade 平仓 | 更新 actual_exit_price, pnl, exit_reason, status=closed |
| 信号过期未入场 | status=expired |

**绩效分析 (analytics.py):**

```python
def calc_performance(days: int = 30) -> dict:
    """计算最近 N 天的绩效"""
    return {
        "total_trades": 42,
        "win_rate": 0.62,
        "avg_pnl_pct": 2.3,
        "max_drawdown_pct": 8.5,
        "profit_factor": 1.8,
        "confidence_calibration": {
            # 置信度 vs 实际胜率
            "70-80": {"count": 15, "actual_win_rate": 0.60},
            "80-90": {"count": 10, "actual_win_rate": 0.70},
            "90+":   {"count": 5,  "actual_win_rate": 0.80},
        },
        "by_symbol": {...},
        "by_direction": {...},
    }
```

**文件变更:**

| 文件 | 变更 |
|------|------|
| `src/cryptobot/journal/` | **新建** — 整个目录 |
| `workflow/graph.py` | execute 中创建 SignalRecord |
| `realtime/monitor.py` | promote 时更新 SignalRecord |
| `cli/` | 新增 `journal` 子命令 (show/stats) |

#### 预期效果

- 完整记录每笔信号从生成到结束的全生命周期
- 置信度校准: 发现 AI 高估/低估置信度的模式
- 可以反馈到 TRADER prompt 中: "你的历史胜率 62%, 近期偏乐观"

---

### 4.3 portfolio.py 统一 FT API

#### 问题现状

`cli/portfolio.py` 仍保留硬编码的 `FT_API_URL/FT_USERNAME/FT_PASSWORD` 和独立的 `_ft_get()` 函数。在 Fix 6 中已创建 `freqtrade_api.py` 统一模块，但 portfolio.py 遗漏未修。

#### 解决方案

与 monitor.py 相同的处理: 删除硬编码常量和 `_ft_get()`，改用 `from cryptobot.freqtrade_api import ft_api_get`。

**文件变更:**

| 文件 | 变更 |
|------|------|
| `cli/portfolio.py` | 删除 FT_API_URL 等常量 + `_ft_get()`，改用 `ft_api_get` |

---

## 五、P2 — 高级策略优化

### 5.1 事件驱动能力

#### 问题现状

当前只有固定 2h 分析周期。极端情况（如 BTC 30 分钟内跌 10%、重大监管新闻）需要等下一个周期才能反应。等待期间:
- 持仓可能已经大幅亏损
- 市场机会可能已经错过

#### 解决方案

新增 `src/cryptobot/events/` 事件监听模块:

```
events/
├── __init__.py
├── price_alert.py    # WebSocket 价格监听
├── news_alert.py     # 新闻轮询 (复用现有 API)
└── dispatcher.py     # 事件分发器
```

**价格告警 (WebSocket):**

```python
# 连接 Binance WebSocket
# 监听所有 10 个交易对的 1m K 线
# 触发条件:
#   - 5 分钟涨跌幅 > 3%
#   - 15 分钟涨跌幅 > 5%
#   - 成交量 > 5 倍均值
```

**事件响应:**

```python
async def on_price_crash(symbol: str, change_pct: float):
    """价格急跌事件"""
    # 1. 如果有同方向持仓 → 触发紧急 re-review
    # 2. 如果跌幅 > 10% → 通知 + 考虑加速止损
    # 3. 如果无持仓 → 触发快速分析 (只对该币种)
```

**文件变更:**

| 文件 | 变更 |
|------|------|
| `src/cryptobot/events/` | **新建** — 事件监听模块 |
| `cli/scheduler.py` | 启动事件监听线程 |
| `pyproject.toml` | 添加 `websockets` 依赖 |

---

### 5.2 AI 决策质量追踪与自我改进

#### 问题现状

8 个 AI 角色的 system prompt 是静态的。无法知道:
- 技术分析师 vs 基本面分析师谁更准确？
- 看多研究员是否倾向过度乐观？
- 风控经理是否过于保守（拒绝太多好交易）或过于宽松？

#### 解决方案

基于 4.2 的交易记录数据，构建反馈循环:

**1. 每周绩效摘要注入 prompt:**

在 TRADER prompt 中动态添加:

```
### 近期表现参考 (近 30 天)
- 总信号: 42 个, 入场: 35 个, 胜率: 62%
- 平均盈亏比: 1.8:1
- 置信度校准: 80+ 实际胜率 70% (略偏乐观)
- 最近 5 笔: BTCUSDT +3.2%, ETHUSDT -1.5%, ...
- 常见失败模式: 追高入场被套、止损设置过紧
```

**2. 分析师权重动态调整:**

在 `trade` 节点 prompt 中，根据各分析师历史准确率标注可信度:

```
"技术分析师 (近期准确率 65%): bullish, confidence 75"
"情绪分析师 (近期准确率 55%): bearish, confidence 60"
```

**依赖:** 需要先完成 4.2 (交易记录)。

**文件变更:**

| 文件 | 变更 |
|------|------|
| `journal/analytics.py` | 添加分析师准确率计算 |
| `workflow/graph.py` | trade() 注入历史绩效摘要 |
| `workflow/prompts.py` | TRADER prompt 模板增加动态部分 |

---

## 六、P3 — 架构增强

### 6.1 独立 K 线数据源

#### 问题现状

`indicators/calculator.py` 中 `load_klines()` 从 Freqtrade 的 feather 文件读取 K 线:

```
user_data/data/binance/futures/{BASE}_USDT_USDT-{tf}-futures.feather
```

这意味着:
- 必须运行 Freqtrade 的 `download-data` 命令才有数据
- AI 工作流无法独立于 Freqtrade 运行

#### 解决方案

`load_klines()` 添加 fallback: feather 不存在时从 Binance REST API 获取:

```python
def load_klines(symbol: str, timeframe: str) -> pd.DataFrame:
    # 1. 先尝试 feather 文件 (现有逻辑)
    # 2. feather 不存在 → 从 Binance /fapi/v1/klines 获取
    # 3. 缓存到 data/output/.cache/klines/ (TTL 根据 timeframe)
```

注意: `multi_timeframe.py` 中 `_fetch_daily_klines()` 已经有类似实现（日线从 Binance 获取），可以复用模式。

**文件变更:**

| 文件 | 变更 |
|------|------|
| `indicators/calculator.py` | `load_klines()` 添加 Binance API fallback |

---

### 6.2 工作流容错增强

#### 问题现状

`collect_data` 节点中，单个数据源失败会被捕获并记录到 errors，但:
- 如果 CoinGlass 全部 429（10 币种全失败），工作流仍会继续分析缺失衍生品数据的币种
- 分析师收到不完整数据可能做出低质量判断
- 没有"数据质量不足则跳过分析"的机制

#### 解决方案

**1. 数据完整性评分:**

```python
def _data_quality_score(symbol_data: dict) -> float:
    """评估单个币种数据完整性 0-100"""
    score = 0
    if symbol_data.get("tech"):
        score += 40  # 技术指标最重要
    if symbol_data.get("crypto"):
        score += 20  # 链上数据
    if symbol_data.get("multi_tf"):
        score += 15  # 多时间框架
    if symbol_data.get("volume_analysis"):
        score += 10
    if symbol_data.get("support_resistance"):
        score += 10
    if symbol_data.get("btc_correlation"):
        score += 5
    return score
```

**2. screen 节点中过滤低质量数据:**

```python
# 数据质量 < 40 的币种不进入分析
# (至少需要技术指标)
if _data_quality_score(data) < 40:
    logger.warning("数据不足跳过 %s (quality=%d)", symbol, quality)
    continue
```

**3. 工作流全局错误阈值:**

```python
# errors > 50% 数据源失败 → 跳过本轮分析
total_sources = len(symbols) * 6  # 每个币种 6 类数据
if len(errors) > total_sources * 0.5:
    logger.error("数据源失败率过高 (%d/%d), 跳过本轮", len(errors), total_sources)
    return {"errors": errors, "skip_reason": "data_quality_insufficient"}
```

**文件变更:**

| 文件 | 变更 |
|------|------|
| `workflow/graph.py` | collect_data 末尾加质量评分 |
| `workflow/graph.py` | screen 中过滤低质量币种 |

---

### 6.3 回测验证 AI prompt

#### 问题现状

8 个 AI 角色的 system prompt 是手工编写的，没有客观验证手段。改一句 prompt 不知道是变好还是变差。

#### 解决方案

构建离线回测框架：用历史 K 线 + 已知结果喂给 AI，对比信号质量。

```
backtest/
├── __init__.py
├── data_loader.py     # 从 Freqtrade SQLite + journal 加载历史数据
├── runner.py          # 批量调用 AI 工作流（用历史数据替代实时采集）
└── evaluator.py       # 对比 AI 信号 vs 实际走势，计算胜率/盈亏比
```

**核心流程:**

```
1. 选取过去 30 天的 N 个时间点
2. 每个时间点：用当时的市场数据构造 prompt 输入
3. 调用 AI 工作流生成信号
4. 用后续价格走势评估信号质量
5. 对比不同 prompt 版本的胜率/盈亏比
```

**依赖:** 需要先完成 4.2 (交易记录) 和 6.1 (独立 K 线数据源)。

**文件变更:**

| 文件 | 变更 |
|------|------|
| `src/cryptobot/backtest/` | **新建** — 回测框架 |
| `cli/` | 新增 `backtest` 子命令 |

---

### 6.4 多策略切换

#### 问题现状

无论市场处于趋势、震荡还是高波动状态，AI 使用同一套 prompt 和参数。实际上：
- 趋势市：应跟随趋势，放宽止盈，收紧止损
- 震荡市：应高抛低吸，缩小仓位，快进快出
- 高波动（黑天鹅）：应降低杠杆，缩小仓位，甚至空仓观望

#### 解决方案

在 `collect_data` 节点检测市场状态，传入不同策略参数：

```python
def _detect_market_regime(market_data: dict) -> str:
    """判断市场状态: trending / ranging / volatile"""
    # BTC ADX > 25 且 EMA 排列一致 → trending
    # BTC ADX < 20 且 BB 窄 → ranging
    # ATR 突增 或 恐惧贪婪 < 20 → volatile
```

不同状态对应不同策略参数：

| 市场状态 | 最大杠杆 | 仓位上限 | 止损系数 | 止盈目标 |
|----------|----------|----------|----------|----------|
| trending | 5x | 80% | 正常 | 放宽 1.5x |
| ranging | 3x | 50% | 收紧 0.7x | 收紧 0.7x |
| volatile | 2x | 30% | 收紧 0.5x | 正常 |

**文件变更:**

| 文件 | 变更 |
|------|------|
| `workflow/graph.py` | collect_data 末尾检测市场状态 |
| `workflow/graph.py` | trade/risk_review 传入状态参数 |
| `workflow/prompts.py` | TRADER/RISK_MANAGER prompt 增加市场状态上下文 |
| `config/settings.yaml` | 添加各状态的策略参数 |

---

## 七、实施路线

按投入产出比排序：

| 阶段 | 内容 | 复杂度 | 影响 |
|------|------|--------|------|
| **P0** | 仓位计算接入 — execute 节点调用 position_sizer，把 pct 转 USDT | 小 | 防爆仓 |
| **P0** | 持仓上下文注入 — TRADER/RISK_MANAGER prompt 携带账户余额和现有持仓 | 小 | 防过度集中 |
| **P0** | 内置调度器 — `cryptobot daemon` 一个命令跑起 2h 分析 + 5min 监控 + 告警 | 中 | 真正自动化 |
| **P1** | Telegram 通知 — 信号写入/止盈触发/爆仓告警/日报推送 | 小 | 你才能放心睡觉 |
| **P1** | 交易记录 + 绩效看板 — 从 Freqtrade SQLite 读取，计算实际胜率/盈亏比 | 中 | 闭环反馈 |
| **P1** | portfolio.py 的 `_ft_get` 替换为 `ft_api_get` | 极小 | 一致性 |
| **P2** | 价格异动事件驱动 — WebSocket 监控 BTC 5min 跌幅 > 3% 触发紧急复审 | 中 | 黑天鹅防护 |
| **P2** | AI 决策质量追踪 — confidence vs 实际结果的校准分析 | 中 | 长期胜率提升 |
| **P3** | 回测验证 AI prompt — 用历史数据喂给 AI，对比信号质量 | 大 | 优化 prompt |
| **P3** | 多策略切换 — 趋势/震荡/高波动不同市场状态用不同策略参数 | 大 | 适应性 |

> **P0 是现在最需要做的 — 不做的话系统跑起来也是赌博。**

### 验证标准

**Phase 1 (P0) 完成后:**
- [ ] AI 生成信号的 position_size_usdt 不再是固定 1000
- [ ] TRADER prompt 中能看到账户余额和现有持仓
- [ ] `max_same_direction_pct: 50%` 在代码层面被 enforce
- [ ] `uv run cryptobot scheduler start` 能自动运行所有任务

**Phase 2 (P1) 完成后:**
- [ ] Telegram 能收到新信号/止损调整/异常告警
- [ ] `uv run cryptobot journal stats` 能展示胜率/盈亏比
- [ ] 置信度校准数据可查

**Phase 3 (P2) 完成后:**
- [ ] BTC 5min 跌幅 > 3% 时自动触发紧急复审
- [ ] confidence 校准报告可查

**Phase 4 (P3) 完成后:**
- [ ] 回测框架能对比不同 prompt 版本的胜率
- [ ] 市场状态检测准确率 > 70%

---

## 八、风险与注意事项

### 8.1 API 配额

| API | 当前用量/轮 | 限制 | 风险 |
|-----|------------|------|------|
| Claude CLI (Max 5x) | ~40 次/2h | 225 次/5h | 低风险 (约 18%) |
| CoinGlass | ~10 次/2h | 取决于套餐 | 中风险 (偶尔 429) |
| CoinGecko (免费) | ~5 次/2h | 10-30 次/min | 低风险 (已有 2s delay) |
| CryptoNews-API | ~5 次/2h | 取决于套餐 | 低风险 |
| Binance (公开) | ~12 次/2h | 1200 次/min | 极低 |

添加事件驱动 (5.1) 后，极端行情可能触发额外 5-10 次 Claude 调用，仍在配额内。

### 8.2 测试策略

每个 Phase 完成后:
```bash
uv run pytest tests/ -v               # 全量测试
uv run ruff check src/                 # lint
uv run cryptobot workflow run --dry-run --json-output  # 端到端烟雾测试
```

新增功能需同步编写测试:
- 仓位计算接入: 测试 _decision_to_signal 带 calc_position_size 的输出
- 通知系统: mock Telegram API 测试消息格式
- 交易记录: 测试 SignalRecord 完整生命周期
- 事件驱动: mock WebSocket 测试事件触发

### 8.3 回滚策略

所有变更应保持向后兼容:
- 仓位计算: 如果获取余额失败，fallback 到现有 1000 USDT
- 通知: 如果 Telegram 未配置，静默跳过
- 事件驱动: 作为可选功能，不影响定时工作流
- 调度器: 现有 CLI 命令保持不变，调度器只是包装

---

## 九、P4 — 实战优化与可观测性

> 基于 2026-02-20 完成全部 P0-P3 后的下一步规划。
> 343 tests passed, 核心模块覆盖率 80%+, dry-run 验证通过。

### 9.1 graph.py 拆分重构

**问题:** `workflow/graph.py` 1295 行，包含 7 个图节点 + re_review + 辅助函数，违反单一职责。

**方案:** 按节点拆分为独立模块：

```
workflow/
├── graph.py          # 图定义 + build_graph() (< 100 行)
├── nodes/
│   ├── collect.py    # collect_data + collect_data_for_symbols + 数据质量
│   ├── screen.py     # screen 筛选逻辑
│   ├── analyze.py    # analyze + research
│   ├── trade.py      # trade 交易决策
│   ├── risk.py       # risk_review 硬性规则 + AI 审核
│   ├── execute.py    # execute 信号写入
│   └── re_review.py  # 独立持仓复审
├── regime.py         # 市场状态检测
├── llm.py            # Claude CLI 封装
└── prompts.py        # AI 角色 prompt
```

**收益:** 每个文件 100-200 行，可独立测试，覆盖率显著提升。

### 9.2 Prompt A/B 测试框架

**问题:** 修改 prompt 后无法量化影响（是变好还是变差）。

**方案:** 基于 `backtest/evaluator.py` 扩展：

```python
# backtest/ab_test.py
def run_ab_test(prompt_a: str, prompt_b: str, test_cases: list[dict]) -> dict:
    """对比两个 prompt 版本在历史数据上的信号质量"""
    # 用历史 market_data 调用 AI，对比胜率/盈亏比/置信度校准
```

**关键前置:** 需要积累 50+ 已平仓 journal 记录作为 ground truth。

### 9.3 Web Dashboard (轻量监控面板)

**问题:** 所有输出都是 CLI，无法远程查看状态。

**方案:** 最小化 FastAPI + HTMX 面板：

```
dashboard/
├── app.py            # FastAPI 应用 (< 200 行)
├── templates/
│   └── index.html    # HTMX 单页面
└── api.py            # JSON API 端点
```

展示内容:
- 当前持仓 + 盈亏
- 最近信号列表
- journal 绩效图表
- 告警历史
- 调度器状态

**依赖:** `fastapi` + `uvicorn` (可选安装)

### 9.4 WebSocket 替代轮询

**问题:** realtime/monitor.py (10s 轮询) 和 events/price_monitor.py (30s 轮询) 都用 REST 轮询，延迟高且浪费 API 配额。

**方案:** 使用 Binance WebSocket Streams:

```python
# 连接 wss://fstream.binance.com/stream
# 订阅: {symbol}@kline_1m + {symbol}@markPrice@1s
```

**收益:** 实时价格推送 (< 100ms 延迟)，零 REST API 调用。

### 9.5 AI 角色历史准确率反馈

**问题:** AI 角色 prompt 是静态的，不知道自己历史表现。

**方案:** 在 `trade()` prompt 中注入动态绩效上下文：

```
### 近期表现 (近 30 天)
- 总信号 42 个, 胜率 62%, 盈亏比 1.8:1
- 置信度校准: 80+ 实际胜率 70% (略偏乐观)
- 最近连续: 3 连胜
- 常见失败模式: 震荡市追突破被假突破套
```

**依赖:** `journal/analytics.py` 已实现，只需在 `graph.py:trade()` 注入。

### 9.6 配置热更新

**问题:** 修改 settings.yaml 后需要重启 daemon。

**方案:** 在调度器中每 5min 检查配置文件 mtime，变化时重新加载：

```python
# scheduler.py
_settings_mtime = 0
def _maybe_reload_settings():
    mtime = Path("config/settings.yaml").stat().st_mtime
    if mtime > _settings_mtime:
        reload...
```

### 实施优先级

| 编号 | 内容 | 复杂度 | 收益 |
|------|------|--------|------|
| 9.5 | AI 历史准确率反馈 | 小 | 直接提升信号质量 |
| 9.1 | graph.py 拆分 | 中 | 可维护性 + 测试覆盖率 |
| 9.6 | 配置热更新 | 小 | 运维便利 |
| 9.3 | Web Dashboard | 中 | 远程监控 |
| 9.2 | Prompt A/B 测试 | 中 | 长期优化 |
| 9.4 | WebSocket 替代轮询 | 大 | 延迟降低 |

---

## 十、当前系统状态 (2026-02-20)

### 10.1 完成度

| 阶段 | 状态 | 测试 |
|------|------|------|
| P0 (资金安全 + 自动化) | 完成 | passed |
| P1 (运营闭环) | 完成 | passed |
| P2 (高级策略) | 完成 | passed |
| P3 (架构增强) | 完成 | passed |
| 代码质量 10 Fix | 完成 | passed |

### 10.2 测试覆盖

- 总测试: 343 passed
- 总覆盖率: 59% (核心业务模块 80%+)
- 高覆盖模块: indicators (83-100%), journal (97-100%), risk (97-100%), signal (87%)
- 低覆盖区域: CLI 展示层 (20-29%), workflow/graph.py LLM 集成 (29%)

### 10.3 端到端验证

```
dry-run 验证通过:
- 10 币种数据采集: 全部成功
- 恐惧贪婪指数: 7
- 市场状态检测: ranging (50% 置信度)
- 筛选结果: SOLUSDT, ETHUSDT, BNBUSDT, LINKUSDT, BTCUSDT
- 错误: 0
```

### 10.4 部署文件

```
deploy/
├── preflight.sh                 # 部署前检查脚本
├── com.cryptobot.daemon.plist   # macOS launchd 开机自启
├── cryptobot-daemon.service     # Linux systemd 服务
└── logrotate-cryptobot.conf     # 日志轮转配置
.env.example                     # 环境变量模板
```
