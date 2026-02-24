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
uv run cryptobot backtest run --days 90       # 量化回测 (成本模型+净值曲线+Sharpe)
uv run cryptobot backtest baseline --strategy all  # 基线策略对比
uv run cryptobot backtest compare --days 90   # AI vs 基线统计检验 (p-value)
uv run cryptobot backtest replay-history --days 90  # 历史回放 (LLM 驱动信号生成+回测)
uv run cryptobot backtest replay-history --days 30 --symbols "BTCUSDT,ETHUSDT"  # 指定币种
uv run cryptobot backtest replay-history --preset 180d  # 预设周期回放
uv run cryptobot backtest replay-compare            # 多周期回放结果对比
uv run cryptobot backtest overfit-check             # 过拟合检测
uv run cryptobot backtest walk-forward --days 180   # Walk-forward 滚动验证
uv run cryptobot backtest features                  # 查看最新特征矩阵
uv run cryptobot journal edge                       # Edge 仪表盘 (期望值/SQN/R分布)
uv run cryptobot journal regime-eval                # Regime 感知评估 (按 regime 分组对比绩效)
uv run cryptobot risk symbol-profile                # 币种 A/B/C/D 分级管理
uv run cryptobot features factor-analysis           # 多因子 lead-lag 相关性分析
uv run cryptobot ml train --days 180                # LightGBM 模型训练
uv run cryptobot ml score --symbol BTCUSDT          # 单币种 ML 评分
uv run cryptobot ml evaluate                        # 模型评估报告
uv run cryptobot ml retrain --days 180              # 手动触发模型重训
uv run cryptobot ml history                         # 查看模型版本历史
uv run cryptobot strategy funding-scan              # 扫描资金费率套利机会
uv run cryptobot strategy funding-run               # 执行资金费率套利 (虚拟盘)
uv run cryptobot strategy funding-status            # 查看套利虚拟盘状态
uv run cryptobot strategy grid-create --symbol BTCUSDT  # 创建网格
uv run cryptobot strategy grid-status               # 查看网格状态
uv run cryptobot strategy grid-check                # 检查网格触发
uv run cryptobot strategy portfolio --strategy funding_arb  # 虚拟盘总览
uv run cryptobot strategy weights                   # 查看当前策略权重分配
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
AI 工作流 (30min 周期)                    实时监控 (持续)              Freqtrade (5m K线)
─────────────────────                ──────────────              ────────────────
collect → screen ──┬→ analyze →      pending_signals.json
                   │  research ──┐   轮询 Binance (10s)
                   └→ trade ←────┘   价格进入 entry_range?
                      ml_filter →    5m 指标确认?
                      risk_review →  └─写入→ signal.json  ─读取→  AgentSignalStrategy
                      execute
```

`screen → analyze` 有条件路由：volatile 时若所有币种不需 LLM 则直接跳到 trade。

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
| `workflow/graph.py` | LangGraph 8 节点状态图(含 ml_filter) + `should_analyze` 条件路由(volatile 时跳过 LLM) + 独立 `re_review()` 持仓复审流程 |
| `workflow/llm.py` | Claude CLI 子进程封装，内置速率限制和重试，支持 `role` 参数路由模型 |
| `workflow/api_llm.py` | OpenAI 兼容 API 后端（DeepSeek/OpenAI/Groq 等），支持角色级模型选择 + token 用量追踪 |
| `workflow/prompts.py` | 7 个 AI 角色的 system prompt + JSON schema（含 RE_REVIEWER） |
| `signal/bridge.py` | signal.json / pending_signals.json 读写校验 + `update_signal_field` 动态更新 + fcntl 文件锁(跨进程安全) + 强制止损校验 |
| `realtime/monitor.py` | 轮询 Binance 价格，等待入场区间 + 5m 指标确认后 promote 信号 |
| `indicators/calculator.py` | 技术指标计算（TA-Lib），K 线数据加载（feather 优先 + Binance API fallback） |
| `indicators/multi_timeframe.py` | 多时间框架共振、量价分析、支撑阻力 |
| `indicators/hurst.py` | Hurst 指数 (R/S 分析): H>0.55 趋势/H<0.45 均值回归，加权投票增强 regime 检测 |
| `data/` | 外部数据获取：链上(CoinGlass)、情绪(Fear&Greed)、新闻(CryptoNews-API)、稳定币流(DefiLlama)、订单簿(Binance)、交易所储备(CoinGlass)、宏观日历(FinnHub)、期权(Deribit)、代币稀释(CoinGecko)、DXY美元指数(Yahoo Finance)、DeFi TVL(DefiLlama)、巨鲸追踪(Whale Alert) |
| `regime_smoother.py` | 市场状态转换平滑：连续 N 周期确认才切换 regime，防止边界反复跳动 |
| `workflow/strategy_router.py` | Regime 感知策略路由: trending→AI趋势 / ranging→均值回归 / volatile→三子状态(normal→保守AI趋势, fear→套利+网格+趋势空头fallback, greed→仅做空) + 多策略权重分配 route_strategies() |
| `workflow/nodes/ml_filter.py` | ML 信号过滤节点: trade→ml_filter→risk_review，方向一致性+概率阈值过滤 |
| `ml/feature_feedback.py` | ML 特征重要性反馈: top-5 特征按角色注入分析师 prompt |
| `ml/retrainer.py` | 模型自动重训: 每周日重训 + AUC 对比回滚 + 版本管理 |
| `ml/registry.py` | 模型版本注册表: ModelRecord + 原子写入持久化 |
| `strategy/weight_tracker.py` | 策略权重管理: 按 regime 动态分配多策略权重(trending 80/20, ranging 50/30/20, volatile_normal/fear/greed 三组) |
| `strategy/mean_reversion.py` | BB 均值回归策略: 下轨+RSI<35 做多 / 上轨+RSI>65 做空，仅 ranging 时启用 |
| `strategy/virtual_portfolio.py` | 虚拟盘基础设施：不可变 VirtualPortfolio/VirtualPosition + 原子写入持久化 |
| `strategy/funding_arb.py` | 资金费率套利：扫描正费率 → delta 中性开仓 → 费率转负平仓 + 费率反转保护 + volatile_mode 绕过全局 enabled (虚拟盘) |
| `strategy/grid_trading.py` | 网格交易：支撑阻力间等距网格 + 价格触发自动买卖 (虚拟盘) |
| `capital_strategy.py` | 资金感知策略：根据余额自动调整层级(micro/small/medium/large)，与 regime 正交叠加取更严格值 |
| `evolution/prompt_manager.py` | Prompt 版本管理：版本化存储/切换/对比 addon，持久化 `prompt_versions.json` |
| `evolution/regime_prompts.py` | Regime 级 Prompt Addon：趋势市/震荡市/高波动市分别注入不同偏好到 trader/analyst |
| `evolution/capital_prompts.py` | 资金层级 Prompt Addon：micro/small 层级注入保守偏好到 trader/analyst/risk_manager |
| `evolution/prompt_optimizer.py` | 绩效驱动 Prompt 自动迭代：检测退化 → 分析失败 → AI 生成改进 → 创建新版本 |
| `evolution/strategy_advisor.py` | 策略顾问 Agent：绩效模式发现 → 规则生成 → Prompt Addon 注入 → 14天评估 → 续期/淘汰 |
| `evolution/model_competition.py` | 多模型竞赛：并行调用多模型决策，consensus/best_performer 策略择优（2 模型分歧时选 no_trade 保守策略） |
| `volatile_toggle.py` | Volatile 策略自适应开关：根据虚拟盘绩效自动启停 volatile 子策略 |
| `journal/regime_evaluator.py` | Regime 感知评估：按市场状态分组 Welch t-test 对比前后绩效 |
| `risk/position_sizer.py` | 仓位计算：Kelly 5级 fallback + 相关性折算 + 波动率自适应杠杆 + 币种分级杠杆限制 |
| `risk/monthly_circuit_breaker.py` | 月度亏损熔断：连续 2 月亏损降仓 50%+暂停做多; 连续 3 月暂停 7 天 |
| `risk/symbol_profile.py` | 币种差异化：按 180d 历史 A/B/C/D 四档分级，差异化杠杆/置信度/过滤 |
| `risk/correlation.py` | 跨币种 Pearson 相关性矩阵 + 组合风控（高相关同向限仓） |
| `risk/execution_optimizer.py` | 执行窗口优化 + 资金费率调度 + 滑点估算 |
| `risk/liquidation_calc.py` | 爆仓距离计算 + 预警分级 |
| `notify.py` | Telegram 通知：信号/风控/告警/日报/错误推送（silent fallback） |
| `telegram/bot.py` | Telegram Bot 长轮询：接收命令 → handlers 处理 → 回复 |
| `telegram/handlers.py` | 11 个命令处理器 + Freqtrade 离线自动 fallback 虚拟盘（positions/balance/liq） |
| `journal/` | 交易记录与绩效：SignalRecord 生命周期(含 model_id) + 胜率/盈亏比/置信度校准 + prompt 注入 + 分析师动态权重 + 动态置信度阈值 |
| `events/` | 价格异动监控：30s 轮询检测 5min/15min 大幅波动 → 紧急复审 + 通知 |
| `journal/edge.py` | Edge 仪表盘：期望值/SQN/R分布/Regime分组/7d-vs-30d对比/衰减检测 |
| `cli/scheduler.py` | APScheduler 调度器：12 个定时任务(含日报 cron + prompt 优化 + 过拟合检查 + ML 周重训) + 可选事件监控线程 |
| `cli/prompt.py` | Prompt 版本管理 CLI：list/new-version/activate/show |
| `backtest/evaluator.py` | 信号回测评估：胜率/盈亏比/连胜连败 + K 线复盘(MFE/MAE) |
| `backtest/cost_model.py` | 交易成本建模：手续费/滑点/资金费率，杠杆敏感 + volatile 滑点 3x 乘数 |
| `backtest/trade_simulator.py` | 逐根 1h K 线扫描，分批止盈，MFE/MAE，MFE 自适应尾随止损(2×ATR 保本)，净 PnL |
| `backtest/equity_tracker.py` | 净值曲线 + Sharpe/Sortino/MaxDD/Calmar/月度收益 |
| `backtest/baselines.py` | 随机/MA交叉/RSI/布林通道 4 种基线信号生成 |
| `backtest/stats.py` | Welch's t-test + Permutation test 统计检验 (无 scipy) |
| `backtest/engine.py` | 完整回测编排：信号加载→模拟→统计→报告持久化 |
| `backtest/historical_replay.py` | 历史回放引擎：历史K线→技术快照→LLM批次决策→信号生成→交易模拟（断点续跑，按配置隔离进度） |
| `backtest/bootstrap.py` | Percentile bootstrap 置信区间（纯 Python），支持 mean/median/win_rate/sharpe/profit_factor |
| `backtest/walk_forward.py` | Walk-forward 滚动验证: 60d 训练/30d 测试/30d 步进，IS/OOS Sharpe 对比防过拟合 |
| `backtest/_sharpe_utils.py` | 统一 Sharpe 年化工具: `annualize_sharpe(returns, trades_per_year)` 全局复用 |
| `backtest/replay_comparator.py` | 多周期回放对比：Sharpe/胜率 CV + 稳定性评级(A/B/C/D) |
| `evolution/overfit_detector.py` | 过拟合检测：修改频率+绩效趋势+规则稳定性+IS/OOS Sharpe 退化检测 评分(0-100) |
| `features/extractors.py` | 7 个特征提取器：技术/多TF/链上/情绪/订单簿/宏观/相关性 |
| `features/pipeline.py` | 特征管道：FeatureVector/FeatureMatrix + z_score/min_max 标准化 |
| `features/feature_store.py` | 特征持久化：按日期存储/加载/清理(保留90天) |
| `features/factor_analysis.py` | 多因子分析：因子×多 lag lead-lag Pearson 相关性 + p-value 显著性筛选 |
| `ml/lgb_scorer.py` | LightGBM 信号评分：特征→涨跌概率分类器，TimeSeriesSplit CV 训练/评估/模型持久化 + hold-out AUC 报告 |
| `cli/doctor.py` | 12 项环境健康检查（Python/TA-Lib/API/目录等） |
| `cli/init_cmd.py` | 环境初始化：创建目录 + .env + 交互 API key + doctor |
| `archive/` | AI 决策归档：每轮工作流保存完整决策链(筛选评分/分析/风控细节/信号)到 JSON，支持 CLI 查阅 |
| `cli/` | Click 命令组，27 个子命令 |
| `web/routes/api.py` | Dashboard API：仪表盘/信号/持仓/告警/绩效 + K 线数据 + 交易历史 |
| `freqtrade_strategies/AgentSignalStrategy.py` | Freqtrade 策略：动态止损(含 Agent 尾随 + MFE 2×ATR 保本)、分批止盈(adjust_trade_position)、仓位控制 |

### 数据文件路径约定

- Freqtrade K 线 feather: `user_data/data/binance/futures/{BASE}_USDT_USDT-{tf}-futures.feather`（备选路径 `user_data/data/futures/`）
- 信号输出: `data/output/signals/signal.json`、`pending_signals.json`
- 缓存: `data/output/.cache/`（各数据源子目录: `stablecoin/`、`exchange_reserve/`、`orderbook/`、`coinglass/`、`dxy/`、`defi_tvl/`、`whale/` 等）
- 分析师权重: `data/output/evolution/weights.json`
- Regime 历史: `data/output/evolution/regime_history.json`
- Prompt 版本: `data/output/evolution/prompt_versions.json`
- Prompt 迭代记录: `data/output/evolution/iterations.json`
- 模型竞赛记录: `data/output/evolution/competition.json`
- 策略规则: `data/output/evolution/strategy_rules.json`
- 决策归档: `data/output/archive/{YYYY-MM}/{run_id}.json`
- 回测报告: `data/output/backtest/bt_{timestamp}.json`
- 特征矩阵: `data/output/features/{date}.json`
- 相关性矩阵: `data/output/evolution/correlation.json`
- 币种分级: `data/output/evolution/symbol_profiles.json`
- 因子分析: `data/output/evolution/factor_analysis.json`
- ML 模型: `data/output/ml/models/`
- ML 模型注册表: `data/output/ml/registry.json`
- 策略权重: `data/output/evolution/strategy_weights.json`
- 虚拟盘: `data/output/virtual/{strategy}_portfolio.json`
- 网格状态: `data/output/virtual/grid_{symbol}_state.json`
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
