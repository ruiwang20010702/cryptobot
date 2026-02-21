# CryptoBot 生产就绪审计 — 问题清单

> 审计日期: 2026-02-21 | 审查范围: 全部源码 93 个文件
> 修复完成: 2026-02-21 | 47/62 已修复 (13 CRITICAL + 14 HIGH + 20 MEDIUM)
> 状态标记: [ ] 待修复 | [x] 已修复 | [-] 不修复(接受风险)

---

## CRITICAL（13 个 — 直接威胁资金安全或系统崩溃）

### C1. re_review.py 解包数量不匹配 — 持仓复审必定崩溃
- **文件**: `src/cryptobot/workflow/re_review.py:26-28`
- **问题**: `fetch_market_data()` 返回 7 元素 tuple（含 macro_events），但 `collect_data_for_symbols()` 只解包 6 个变量，每次调用必抛 `ValueError: too many values to unpack`
- **影响**: 持仓复审功能完全不可用，已有持仓无法被 AI 复审调整止损，方向反转时亏损扩大
- **修复**: 改为 `market_data, fear_greed, market_overview, global_news, _stablecoin, _macro, errors = fetch_market_data(symbols)`
- **工作量**: 1 行
- [x] 已修复 (commit a564df8)

### C2. AI modified 模式允许任意字段覆盖 — 可被 AI 幻觉绕过风控
- **文件**: `src/cryptobot/workflow/nodes/risk.py:376-379`
- **问题**: 风控 AI 返回 `"decision": "modified"` 时，`adjustments` 字典直接覆盖 `decision` 中任意已有字段（action/confidence/symbol/current_price 等），且在硬性检查之后执行
- **影响**: AI 幻觉可能将 no_trade 改为 long、将止损设为 0、将杠杆设为 999，完全绕过硬性风控
- **修复**: 定义白名单 `ADJUSTABLE_FIELDS = {"leverage", "stop_loss", "take_profit", "position_size_pct", "entry_price_range"}`，只允许修改这些字段，修改后重新校验杠杆上限和止损方向
- **工作量**: ~15 行
- [x] 已修复 (commit a564df8)

### C3. 亏损限制计算方法有误 — 可能过早或过晚触发保护
- **文件**: `src/cryptobot/workflow/nodes/risk.py:41`
- **问题**: `total_pnl_pct = avg_pnl_pct * closed` 用"平均单笔 PnL% x 笔数"估算总亏损，这不是账户级亏损百分比。3 笔各亏 2%，total=-6% 会触发 5% 日度限制，但如果每笔仓位只占 20%，实际账户亏损仅 1.2%
- **影响**: 风控保护的触发时机不准确，可能误触发（拒绝正常交易）或漏触发（真正大亏时放行）
- **修复**: 从交易记录累加每笔 `actual_pnl_usdt`，除以账户余额得到真实亏损百分比
- **工作量**: ~15 行
- [x] 已修复 (commit a564df8)

### C4. 信号文件并发竞态 — 可能丢失信号
- **文件**: `src/cryptobot/signal/bridge.py:63-82`, `159-191`, `196-215`
- **问题**: `write_signal`/`update_signal_field`/`cleanup_expired` 均为读-改-写无锁操作。daemon 模式下工作流、实时监控、cleanup 可能并发操作同一文件
- **场景**: 线程 A 读取 [BTCUSDT] -> 线程 B 读取 [BTCUSDT] -> A 写入 [BTCUSDT, ETHUSDT] -> B 写入 [BTCUSDT, SOLUSDT] -> ETHUSDT 丢失
- **影响**: 信号丢失意味着 AI 认为应该交易但 Freqtrade 永远收不到信号
- **修复**: 添加 `threading.Lock` 保护所有信号文件的读改写操作
- **工作量**: ~15 行
- [x] 已修复 (commit a564df8)

### C5. 仓位 min_amount 绕过风控 — 小账户单笔风险可达 50%
- **文件**: `src/cryptobot/risk/position_sizer.py:83`
- **问题**: `margin_amount = max(margin_amount, min_amount)` 当风控计算出 2U 但 min_amount=50U 时，强制提升到 50U。100U 账户 + 5x 杠杆 = 250U 名义仓位，亏损远超 2% 单笔上限
- **影响**: micro 层级用户的风控形同虚设
- **修复**: 如果 `margin_amount < min_amount`，返回仓位 0（不开仓），而非强行放大
- **工作量**: ~5 行
- [x] 已修复 (commit a564df8)

### C6. stop_loss 不是 TRADE_SCHEMA 必填字段 — AI 可输出无止损信号
- **文件**: `src/cryptobot/workflow/prompts.py:277-305`
- **问题**: TRADE_SCHEMA 的 required 只有 `["action", "confidence", "reasoning"]`，AI 可返回无 entry_price_range、无 stop_loss 的交易决策
- **影响**: 无止损信号写入后，Freqtrade 按默认 -5% 止损，5x 杠杆下单笔亏损 25%
- **修复**: 将 `entry_price_range`、`stop_loss` 加入 required，或在 trade 节点解析时缺少止损则强制标记为 no_trade
- **工作量**: ~10 行
- [x] 已修复 (commit a564df8)

### C7. settings.yaml 中 regime 参数完全未生效 — 用户配置无效
- **文件**: `src/cryptobot/workflow/nodes/collect.py:18-37` vs `config/settings.yaml:120-134`
- **问题**: `_detect_market_regime()` 直接使用硬编码的 `_REGIME_PARAMS`，完全忽略 settings.yaml 中 `market_regime` 下用户自定义的 min_confidence/max_leverage 等值
- **影响**: 用户修改 settings.yaml 中的 regime 参数不会产生任何效果，文档与实际行为不一致
- **修复**: 在 `_detect_market_regime` 中从 settings 读取并与默认值合并
- **工作量**: ~10 行
- [x] 已修复 (commit a564df8)

### C8. onchain.py 三个函数无异常处理 — 网络失败级联崩溃
- **文件**: `src/cryptobot/data/onchain.py:24-28`, `67-73`, `112-118`
- **问题**: `get_funding_rate`/`get_open_interest_hist`/`get_taker_buy_sell_ratio` 的 `httpx.get` + `raise_for_status()` 无 try/except，且 `crypto_specific.py` 串行调用这三个函数也无 try/except
- **影响**: Binance API 任一失败 -> 整个 crypto 指标板块全部丢失，10 个币种的链上数据全无
- **修复**: 为每个函数添加异常捕获返回空结果，或在 `crypto_specific.py` 中单独 try/except 每个调用
- **工作量**: ~20 行
- [x] 已修复 (commit a564df8)

### C9. sentiment.py 的 Fear&Greed 无异常处理 — 影响 regime 检测
- **文件**: `src/cryptobot/data/sentiment.py:26-33`
- **问题**: `get_fear_greed_index` 的网络请求无异常保护，`get_long_short_ratio`/`get_top_trader_long_short` 同样问题
- **影响**: Fear&Greed 数据决定是否将 regime 升级为 volatile，该函数崩溃会导致 regime 检测失败
- **修复**: 添加 try/except 返回包含默认值的字典
- **工作量**: ~15 行
- [x] 已修复 (commit a564df8)

### C10. Journal storage 并发竞态 — 交易记录可能丢失
- **文件**: `src/cryptobot/journal/storage.py:34-45`, `81-102`
- **问题**: `save_record`/`update_record` 都是 load-modify-write 无锁操作，`job_journal_sync`、工作流、Web API 可能并发操作 records.json
- **影响**: 交易记录丢失导致绩效统计不准确，进而影响亏损限制检查和分析师权重计算
- **修复**: 使用 `threading.Lock` 或 `filelock` 保护读写操作
- **工作量**: ~15 行
- [x] 已修复 (commit a564df8)

### C11. 缓存 set_cache 直接修改传入 dict — 违反不可变原则
- **文件**: `src/cryptobot/cache.py:36`
- **问题**: `data["_cached_at"] = time.time()` 直接修改调用方传入的 data 对象，被污染的 result 返回给上层
- **影响**: 上层对返回 dict 做 schema 校验时多出 `_cached_at` 字段可能失败
- **修复**: 写缓存前复制一份 `cached = {**data, "_cached_at": time.time()}`
- **工作量**: 3 行
- [x] 已修复 (commit a564df8)

### C12. news.py (CoinGecko) 三个函数无异常处理
- **文件**: `src/cryptobot/data/news.py:44`, `71`, `115`
- **问题**: `get_market_overview`/`get_coin_info`/`get_trending` 网络失败直接抛异常。CoinGecko 免费 API 有 30 req/min 限制，10 币种分析时容易 429
- **影响**: 一个 429 错误级联导致代币稀释风险评估也失败
- **修复**: 添加 try/except 返回降级结果
- **工作量**: ~15 行
- [x] 已修复 (commit a564df8)

### C13. API PATCH /signals/{symbol} 异常返回 500 非 400
- **文件**: `src/cryptobot/web/routes/api.py:133-147`
- **问题**: `update_signal_field` 抛出 `ValueError` 时未在 API 层捕获，导致 500 Internal Server Error；`value` 类型未校验
- **影响**: 攻击者可传入异常值；500 错误泄露内部信息
- **修复**: 用 try/except 捕获 ValueError 转为 HTTPException(400)，对 updates 使用 Pydantic model 校验
- **工作量**: ~15 行
- [x] 已修复 (commit a564df8)

---

## HIGH（14 个 — 可能导致非预期交易或数据不可靠）

### H1. execute 节点不检查已有持仓 — 可能重复开仓
- **文件**: `src/cryptobot/workflow/nodes/execute.py:35-42`
- **问题**: execute 遍历 approved_signals 写入信号，但不检查 Freqtrade 中是否已持有该币种仓位
- **修复**: 写入前检查 Freqtrade `/status` 中是否已持有同方向仓位
- [x] 已修复 (commit a564df8)

### H2. WS 价格缓存无时效性检查 — 断线期间用过时价格
- **文件**: `src/cryptobot/realtime/ws_price_feed.py:14-22`
- **问题**: `price_cache` 只存 `symbol -> price`，无时间戳。断线重连期间缓存过时但调用方无法判断
- **修复**: 缓存改为 `symbol -> (price, timestamp)`，读取时检查时效
- [x] 已修复 (commit a564df8)

### H3. 持仓数检查未随批次更新 — 同批信号可超限
- **文件**: `src/cryptobot/workflow/nodes/risk.py:295`
- **问题**: `len(positions) >= max_pos` 是开始时快照，循环中不更新。仓位金额有更新但持仓数量没有
- **修复**: 维护 `approved_count` 计数器
- [x] 已修复 (commit a564df8)

### H4. 认证中间件安全问题 — 时序攻击 + testclient 白名单
- **文件**: `src/cryptobot/web/app.py:42-67`
- **问题**: Token 用 `!=` 比较（时序攻击），应用 `hmac.compare_digest`；`testclient` 在生产白名单中
- [x] 已修复 (commit a564df8)

### H5. Freqtrade 密码明文存储
- **文件**: `src/cryptobot/freqtrade_api.py:24`
- **问题**: 从 settings.yaml 读取而非环境变量
- **修复**: 改为从环境变量 `FREQTRADE_PASSWORD` 读取
- [x] 已修复 (commit a564df8)

### H6. exchange_reserve 实际返回 OI 数据 — 语义完全错误
- **文件**: `src/cryptobot/data/exchange_reserve.py:62-64`
- **问题**: 函数名"交易所储备量"但请求的是 `futures/openInterest/chart`（未平仓合约）
- **影响**: AI 分析师根据"交易所储备减少"（实际是 OI 变化）做出错误判断
- **修复**: 改用正确 API 或重命名函数/字段为 `open_interest_trend`
- [x] 已修复 (commit 53db3e5)

### H7. multi_timeframe 运算符优先级隐患
- **文件**: `src/cryptobot/indicators/multi_timeframe.py:39`
- **问题**: 三元表达式优先级导致意图不清；macd_hist 长度为 0 时 IndexError
- **修复**: 拆分独立赋值，增加长度检查
- [x] 已修复 (commit 53db3e5)

### H8. regime volatile 升级不写入历史 — 影响后续平滑
- **文件**: `src/cryptobot/regime_smoother.py:60-62`
- **问题**: volatile 升级时直接 return，不更新 `regime_history.json`
- **修复**: 跳过计数确认但仍更新 history 文件
- [x] 已修复 (commit 53db3e5)

### H9. 爆仓距离计算偏乐观 — 可能低估风险
- **文件**: `src/cryptobot/risk/liquidation_calc.py:59-82`
- **问题**: 未考虑累计维持保证金额、开平仓手续费、资金费率；notional fallback 用 `entry_price * leverage` 无物理意义
- **修复**: 参考 Binance 官方公式使用维持保证金额(cum)；增加 safety_buffer
- [x] 已修复 (commit 53db3e5)

### H10. config.py 无配置验证 — 错误配置静默生效
- **文件**: `src/cryptobot/config.py:36-40`
- **问题**: YAML 语法错误未捕获、类型不校验、必填项缺失静默返回空、数值范围不检查
- **修复**: 添加配置 schema 验证
- [x] 已修复 (commit 53db3e5)

### H11. 离线默认 $1000 余额 — 误导 AI prompt
- **文件**: `src/cryptobot/capital_strategy.py:167-180`
- **问题**: Freqtrade 离线时返回 $1000，capital_tier 基于假余额计算
- **修复**: 离线返回 0 或使用最保守 micro 层级
- [x] 已修复 (commit 53db3e5)

### H12. .env 引号不处理 — API 认证可能失败
- **文件**: `src/cryptobot/config.py:22-26`
- **问题**: `KEY="value"` 解析后 value 含双引号
- **修复**: 去除首尾引号
- [x] 已修复 (commit 53db3e5，与 H10 一并修复)

### H13. Freqtrade API 吞掉所有错误
- **文件**: `src/cryptobot/freqtrade_api.py:26-40`
- **问题**: ConnectError 和 HTTPStatusError 都返回 None，无法区分
- **修复**: 对 HTTPStatusError 记录状态码，401/403 记 ERROR 日志
- [x] 已修复 (commit 53db3e5)

### H14. 价格异动监控数据不足时可能误判
- **文件**: `src/cryptobot/events/price_monitor.py:40-59`
- **问题**: 刚启动时数据点不足，正常波动误判为异动
- **修复**: 增加最小数据覆盖率检查
- [x] 已修复 (commit 53db3e5)

---

## MEDIUM（20 个 — 逻辑不够健壮 / 代码质量）

### M1. 亏损限制异常时 fail-open — 应偏向安全
- **文件**: `src/cryptobot/workflow/nodes/risk.py:44-45`
- [x] 已修复 (commit 53db3e5)

### M2. 爆仓距离无硬性开仓门槛
- **文件**: `src/cryptobot/workflow/nodes/risk.py:332-337`
- **修复**: 增加硬性规则：爆仓距离 < 20% 自动拒绝
- [x] 已修复 (commit 53db3e5)

### M3. 风控直接修改 decision dict — 违反不可变原则
- **文件**: `src/cryptobot/workflow/nodes/risk.py:328`
- [x] 已修复 (commit 53db3e5)

### M4. 缓存清理未覆盖所有子目录
- **文件**: `src/cryptobot/cache.py:43-46`
- [x] 已修复 (commit 53db3e5)

### M5. load_settings() 每次读磁盘无缓存
- **文件**: `src/cryptobot/config.py:36-40`
- [x] 已修复 (commit 53db3e5)

### M6. journal_sync 交易匹配过于简单 — 可能误匹配
- **文件**: `src/cryptobot/cli/scheduler.py:302-338`
- [x] 已修复 (commit 53db3e5)

### M7. WS 重连无 jitter
- **文件**: `src/cryptobot/realtime/ws_price_feed.py:83-88`
- [x] 已修复 (commit 53db3e5)

### M8. validate_signal 缺少 entry_price_range 有效性校验
- **文件**: `src/cryptobot/signal/bridge.py:85-127`
- [x] 已修复 (commit 53db3e5)

### M9. validate_signal 止损校验逻辑不完整
- **文件**: `src/cryptobot/signal/bridge.py:103-108`
- [x] 已修复 (commit 53db3e5)

### M10. danger/critical 阈值重叠
- **文件**: `config/settings.yaml:73-74`
- [x] 已修复 (commit 53db3e5)

### M11. prompt_version 缓存在 daemon 中不刷新
- **文件**: `src/cryptobot/workflow/prompts.py:10-22`
- [x] 已修复 (commit 53db3e5)

### M12. LLM provider 缓存同样问题
- **文件**: `src/cryptobot/workflow/llm.py:32-41`
- [x] 已修复 (commit 53db3e5)

### M13. 5m 指标数据时效未检查
- **文件**: `src/cryptobot/realtime/monitor.py:70-101`
- [x] 已修复 (commit 53db3e5)

### M14. update_signal_field 原地修改列表元素
- **文件**: `src/cryptobot/signal/bridge.py:179-184`
- [x] 已修复 (commit 53db3e5)

### M15. collect_data 中 regime 结果被直接修改
- **文件**: `src/cryptobot/workflow/nodes/collect.py:181-186`
- [x] 已修复 (commit 53db3e5)

### M16. analyze 节点将 error 结果也加入 analyses
- **文件**: `src/cryptobot/workflow/nodes/analyze.py:164-168`
- [x] 已修复 (commit 53db3e5)

### M17. MAX_LEVERAGE 硬编码不与配置同步
- **文件**: `src/cryptobot/signal/bridge.py:22`
- [x] 已修复 (commit 53db3e5)

### M18. --run-now 与定时任务可能重叠执行
- **文件**: `src/cryptobot/cli/scheduler.py:526-533`
- [x] 已修复 (commit 53db3e5)

### M19. Telegram 通知无重试机制
- **文件**: `src/cryptobot/notify.py:49-65`
- [x] 已修复 (commit 53db3e5)

### M20. klines API 的 interval 参数未白名单校验
- **文件**: `src/cryptobot/web/routes/api.py:89`
- [x] 已修复 (commit a564df8，与 C13 一并修复)

---

## LOW（15 个 — 代码质量 / 可维护性）— 暂不修复，接受风险

### L1. _signal_cache 是类变量而非实例变量
- **文件**: `freqtrade_strategies/AgentSignalStrategy.py:53-54`
- [-] 不修复（单实例运行，无影响）

### L2. 时区不一致可能导致 TypeError
- **文件**: `src/cryptobot/signal/bridge.py:40-44`
- [-] 不修复（已全局统一 UTC）

### L3. WS 价格覆盖率 80% 阈值偏低
- **文件**: `src/cryptobot/events/price_monitor.py:88`
- [-] 不修复（可接受）

### L4. 爆仓风险评估阈值不随杠杆调整
- **文件**: `src/cryptobot/risk/liquidation_calc.py:95-111`
- [-] 不修复（H9 safety_buffer 已缓解）

### L5. freqtrade_api 每次调用重新读 settings
- **文件**: `src/cryptobot/freqtrade_api.py:17`
- [-] 不修复（M5 load_settings 已加 mtime 缓存）

### L6. 多处重复构建 portfolio_context
- **文件**: `risk.py:152`, `trade.py:30`
- [-] 不修复（可读性优先）

### L7. _extract_json 正则贪婪匹配
- **文件**: `src/cryptobot/workflow/llm.py:194`
- [-] 不修复（实践中无误匹配）

### L8. 整数关口计算在小价格币种上异常
- **文件**: `src/cryptobot/indicators/multi_timeframe.py:182-184`
- [-] 不修复（辅助信息，不影响交易决策）

### L9. OBV 背离检测过于简单
- **文件**: `src/cryptobot/indicators/multi_timeframe.py:278-297`
- [-] 不修复（辅助指标，后续迭代优化）

### L10. DXY 使用 Yahoo Finance 非官方 API
- **文件**: `src/cryptobot/data/dxy.py:14`
- [-] 不修复（已有异常降级保护）

### L11. _load_dotenv 在模块导入时执行
- **文件**: `src/cryptobot/config.py:29`
- [-] 不修复（单入口无副作用）

### L12. Dashboard API 暴露完整账户余额
- **文件**: `src/cryptobot/web/routes/api.py:31`
- [-] 不修复（H4 已加固认证，本地访问为主）

### L13. backfill.py 使用 MD5 生成 signal_id
- **文件**: `src/cryptobot/journal/backfill.py:204-206`
- [-] 不修复（非安全场景，仅做唯一标识）

### L14. pairs.yaml 相关性分组是静态硬编码
- **文件**: `config/pairs.yaml:74-88`
- [-] 不修复（后续迭代动态化）

### L15. Pivot Points 假设 6 根 4h K 线 = 1 天
- **文件**: `src/cryptobot/indicators/multi_timeframe.py:161-163`
- [-] 不修复（近似足够，加密市场 24h）

---

## 修复记录

### 第一批 — C1-C6,C11 + C7-C10,C12-C13 + H1-H5 (commit a564df8)
- 17 项修复 (5 CRITICAL / 7 HIGH / 5 MEDIUM)
- 核心安全 + 数据可靠性 + 交易安全

### 第二批 — H6-H14 + M1-M20 (commit 53db3e5)
- 30 项修复 (8 CRITICAL / 9 HIGH / 13 MEDIUM)
- 数据质量 + 全面加固

### LOW — 15 项暂不修复
- 接受风险，后续迭代处理

---

## 统计

| 等级 | 总数 | 已修复 | 不修复 | 待修复 |
|------|------|--------|--------|--------|
| CRITICAL | 13 | 13 | 0 | 0 |
| HIGH | 14 | 14 | 0 | 0 |
| MEDIUM | 20 | 20 | 0 | 0 |
| LOW | 15 | 0 | 15 | 0 |
| **合计** | **62** | **47** | **15** | **0** |
