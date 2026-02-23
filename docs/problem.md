# CryptoBot 审计 — 问题清单

> 第一轮审计: 2026-02-21 | 审查范围: 全部源码 93 个文件
> 第一轮修复: 62/62 全部修复 (13 CRITICAL + 14 HIGH + 20 MEDIUM + 15 LOW)
> 第二轮审计: 2026-02-21 | 审查范围: 盈利能力深度审查 (40+ 核心文件)
> 第二轮发现: 22 个新问题 (5 CRITICAL + 9 HIGH + 8 MEDIUM)
> 第二轮修复: 22/22 全部修复
> 第三轮审计: 2026-02-21 | 审查范围: 盈利能力优化 (信号质量/入场出场/资金效率/数据利用率)
> 第三轮发现: 32 个优化点 (2 极高 + 7 高 + 10 中高 + 9 中 + 4 低中)
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

## LOW（15 个 — 代码质量 / 可维护性）— 全部已修复

### L1. _signal_cache 是类变量而非实例变量
- **文件**: `freqtrade_strategies/AgentSignalStrategy.py:53-54`
- [x] 已修复 (commit 58f228f) — 改为 `__init__` 实例变量

### L2. 时区不一致可能导致 TypeError
- **文件**: `src/cryptobot/signal/bridge.py:40-44`
- [x] 已修复 (commit 58f228f) — 添加 `_ensure_utc()` 防御

### L3. WS 价格覆盖率 80% 阈值偏低
- **文件**: `src/cryptobot/events/price_monitor.py:88`
- [x] 已修复 (commit 58f228f) — 阈值 0.8 → 0.9

### L4. 爆仓风险评估阈值不随杠杆调整
- **文件**: `src/cryptobot/risk/liquidation_calc.py:95-111`
- [x] 已修复 (commit 58f228f) — 增加 `leverage` 参数动态缩放

### L5. freqtrade_api 每次调用重新读 settings
- **文件**: `src/cryptobot/freqtrade_api.py:17`
- [x] 已修复 (commit 58f228f) — 模块级缓存 `_ft_config`

### L6. 多处重复构建 portfolio_context
- **文件**: `risk.py:152`, `trade.py:30`
- [x] 已修复 (commit 58f228f) — 缓存到 state，risk_review 优先读 state

### L7. _extract_json 正则贪婪匹配
- **文件**: `src/cryptobot/workflow/llm.py:194`
- [x] 已修复 (commit 58f228f) — 改用 `json.JSONDecoder().raw_decode()`

### L8. 整数关口计算在小价格币种上异常
- **文件**: `src/cryptobot/indicators/multi_timeframe.py:182-184`
- [x] 已修复 (commit 58f228f) — 添加 `round(..., 8)` 浮点精度保护

### L9. OBV 背离检测过于简单
- **文件**: `src/cryptobot/indicators/multi_timeframe.py:278-297`
- [x] 已修复 (commit 58f228f) — 改用百分比变化 + 1% 最小阈值

### L10. DXY 使用 Yahoo Finance 非官方 API
- **文件**: `src/cryptobot/data/dxy.py:14`
- [x] 已修复 (commit 58f228f) — 增加 7 天过期缓存兜底

### L11. _load_dotenv 在模块导入时执行
- **文件**: `src/cryptobot/config.py:29`
- [x] 已修复 (commit 58f228f) — 延迟到 `load_settings()` 首次调用

### L12. Dashboard API 暴露完整账户余额
- **文件**: `src/cryptobot/web/routes/api.py:31`
- [x] 已修复 (commit 58f228f) — 余额脱敏为层级 + 近似值

### L13. backfill.py 使用 MD5 生成 signal_id
- **文件**: `src/cryptobot/journal/backfill.py:204-206`
- [x] 已修复 (commit 58f228f) — MD5 → SHA-256

### L14. pairs.yaml 相关性分组是静态硬编码
- **文件**: `config/pairs.yaml:74-88`
- [x] 已修复 (commit 58f228f) — `get_correlation_groups()` 优先读动态 JSON

### L15. Pivot Points 假设 6 根 4h K 线 = 1 天
- **文件**: `src/cryptobot/indicators/multi_timeframe.py:161-163`
- [x] 已修复 (commit 58f228f) — 优先使用 1d K 线

---

## 第一轮修复记录

### 第一批 — C1-C6,C11 + C7-C10,C12-C13 + H1-H5 (commit a564df8)
- 17 项修复 (5 CRITICAL / 7 HIGH / 5 MEDIUM)
- 核心安全 + 数据可靠性 + 交易安全

### 第二批 — H6-H14 + M1-M20 (commit 53db3e5)
- 30 项修复 (8 CRITICAL / 9 HIGH / 13 MEDIUM)
- 数据质量 + 全面加固

### 第三批 — L1-L15 (commit 58f228f)
- 15 项修复 (15 LOW)
- 代码质量 + 可维护性

### 第一轮统计

| 等级 | 总数 | 已修复 |
|------|------|--------|
| CRITICAL | 13 | 13 |
| HIGH | 14 | 14 |
| MEDIUM | 20 | 20 |
| LOW | 15 | 15 |
| **合计** | **62** | **62** |

---

# 第二轮审计 — 盈利能力深度审查

> 审计日期: 2026-02-21 | 审查方法: 4 个专项 Agent 并行审查 40+ 核心文件
> 审查维度: 交易决策与风控 / AI 提示词与信号质量 / 技术指标与数据管道 / 执行路径与策略
> 核心目标: 以"能否赚钱"为标准，审查业务逻辑合理性

---

## CRITICAL（5 个 — 直接影响盈亏）

### P1. Kelly 公式参数硬编码，默认值算出 0% 仓位
- **文件**: `src/cryptobot/risk/position_sizer.py:19-20`
- **问题**: 默认 `win_rate=0.4, avg_win_loss_ratio=1.5`，代入 Kelly 公式 `f* = (0.4×1.5-0.6)/1.5 = 0`，Half Kelly = 0%。系统完全依赖固定风险法（2% 最大亏损），不是最优仓位
- **影响**: 胜率 50%+、盈亏比 2.0 时本该加大仓位但不会；胜率 35% 时本该缩小仓位但照常开
- **修复**: 从 `journal/stats` 读取实际历史胜率和盈亏比，传入 `calc_position_size()`；若历史数据不足则使用保守默认值 `win_rate=0.35`
- [x] 已修复 — `_load_kelly_params()` 从 journal 读取历史参数，样本 <10 用保守默认

### P2. 止损未经任何验证 — AI 可输出不合理止损
- **文件**: `src/cryptobot/workflow/nodes/trade.py:187-195`
- **问题**: 系统只检查止损"是否存在"，不验证合理性。可能出现：多单止损在入场价上方（方向错误）、止损距离 0.5%（过紧，噪音扫掉）、止损距离 8%（过宽，亏损超预期）
- **影响**: 不合理止损直接导致亏损或频繁止损
- **修复**: 添加方向一致性检查（long 时 sl < entry）+ ATR 校准（止损距离应为 1.5-3x ATR）+ 最大距离上限（≤ 5%）
- [x] 已修复 — 方向验证 + 距离验证 (0.5%-15%)，不合理则强制 no_trade

### P3. 盈亏比（Risk/Reward）未验证
- **文件**: `src/cryptobot/workflow/nodes/risk.py`
- **问题**: 没有检查盈亏比 ≥ 1.5。AI 可能生成止损 3% + 止盈 2% 的交易（RR=0.67），长期必亏
- **影响**: 即使胜率 55%，盈亏比 < 1 也会亏损
- **修复**: 在 `risk_review` 中计算 `RR = |TP1 - entry| / |entry - SL|`，RR < 1.5 则拒绝
- [x] 已修复 — 硬性规则区域添加 RR >= 1.5 检查

### P4. 分批减仓计算 bug — 基于原始仓位而非剩余仓位
- **文件**: `freqtrade_strategies/AgentSignalStrategy.py:342`
- **问题**: `reduce_amount = trade.stake_amount * (tp_pct / 100)` 使用原始仓位。TP1 减 50% 后，TP2 仍按原始 100% 的 30% 计算，实际减仓 50%+30%=80%，但剩余只有 50%
- **影响**: 多级减仓金额错误，Freqtrade 可能报错或异常平仓
- **修复**: 改为基于剩余仓位计算，或用累积百分比追踪已减仓量
- [x] 已修复 — 追踪已成交 TP 百分比，基于剩余仓位计算减仓

### P5. 无学习反馈环 — 系统永远用初始参数运行
- **文件**: 全局架构缺陷
- **问题**: 历史胜率、盈亏比、分析师准确率从不回馈到 Kelly 参数 / 置信度阈值 / 分析师权重。journal 中有数据但未被消费
- **影响**: 系统无法从错误中改进，不管历史表现如何都用同一套参数
- **修复**: 在 `collect` 节点中从 journal 读取近 30 天绩效，动态调整: (1) Kelly 的 win_rate/ratio (2) regime 的 min_confidence (3) 分析师权重注入 trader prompt
- [x] 已修复 — collect 节点注入 perf_feedback 到 state

---

## HIGH（9 个 — 显著降低盈利效率）

### P6. 置信度阈值过高 — 小账户几乎无法开仓
- **文件**: `src/cryptobot/capital_strategy.py:21-31`, `config/settings.yaml:121-134`
- **问题**: micro 层级 `conf_boost=15`，叠加后: micro+trending=70%, micro+ranging=80%, micro+volatile=85%。$500 以下账户几乎不可能达到 85% 置信度
- **影响**: 账户闲置 = 零收益
- **修复**: 降低基础阈值 (trending: 55→50, ranging: 65→55, volatile: 70→60)；micro 的 conf_boost 15→5
- [x] 已修复 — trending 50, ranging 58, volatile 63；settings.yaml + _REGIME_PARAMS 同步

### P7. micro 层级过度保守 — max_positions=1
- **文件**: `src/cryptobot/capital_strategy.py:21-31`
- **问题**: micro 层级 `max_positions=1` + `max_coins=2`，筛选出 2 个币但只能持仓 1 个，50% 机会浪费
- **影响**: 小账户开仓机会极少
- **修复**: max_positions 1→2；降低 conf_boost 15→5；保留 lev_cap=3 作为风控手段
- [x] 已修复 — conf_boost 15→5, max_positions 1→2

### P8. 入场价格范围被 Freqtrade 忽略
- **文件**: `freqtrade_strategies/AgentSignalStrategy.py:135`
- **问题**: AI 输出 `entry_price_range: [64500, 65500]`，但 Freqtrade 不检查当前价格是否在范围内，直接在下一根 K 线开仓。可能在 66000（范围外）入场
- **影响**: 在不利价位入场，止损距离缩小，增加止损概率
- **修复**: 入场前检查 `current_price` 是否在 `entry_price_range` 内，不在则跳过
- [x] 已修复 — 入场前检查价格在 entry_range ± 50% 容差内

### P9. trailing_stop 激活阈值太低 (2%)
- **文件**: `freqtrade_strategies/AgentSignalStrategy.py:231`
- **问题**: 2% 利润就激活尾随止损，加密市场正常波动 2-3%，容易在小回调时被扫掉
- **影响**: 利润被短期波动切断，无法捕获大趋势
- **修复**: 激活阈值 2%→5%（或根据 ATR 动态调整）
- [x] 已修复 — 激活阈值 2%→5%

### P10. 持仓复审无法执行平仓
- **文件**: `src/cryptobot/workflow/re_review.py`
- **问题**: re_review 只能调整止损（`update_signal_field`），即使 AI 建议"应立即平仓"也无法执行。必须等止损触发或下次全量分析
- **影响**: 风险暴露时间延长，可能错过最佳平仓时机
- **修复**: RE_REVIEWER 输出增加 `action: "close"` 选项，写入 close 信号供 Freqtrade 执行
- [x] 已修复 — close_position 决策写入平仓信号

### P11. Fear&Greed 数据延迟 1-2h — 影响 regime 判断
- **文件**: `src/cryptobot/data/sentiment.py`, `src/cryptobot/workflow/nodes/collect.py`
- **问题**: Fear&Greed 来自 alternative.me，更新周期 1h+。闪崩后反弹时仍显示"极度恐惧"，触发 volatile 升级跳过平滑
- **影响**: regime 误判导致置信度阈值错误（volatile 要求最高），错过反弹行情
- **修复**: (1) 增加链上实时情绪合成指标（资金费率+多空比+波动率加权）作为 Fear&Greed 补充 (2) volatile 升级增加价格确认条件
- [x] 已修复 — 新增 `calc_realtime_sentiment()` 合成实时情绪指标

### P12. K 线数据无新鲜度检查
- **文件**: `src/cryptobot/indicators/calculator.py:36-41`
- **问题**: `load_klines()` 优先读本地 feather 文件，但不检查文件修改时间。Freqtrade 未运行时，本地数据可能天级过期
- **影响**: 技术指标基于过期数据计算，完全失效
- **修复**: 检查 feather 文件的最后修改时间，超过 2 倍 TTL 则跳过本地、直接调 Binance API；加载后检查最后一根 K 线时间
- [x] 已修复 — feather 文件超过 6h 过期则回退 API

### P13. 爆仓距离硬编码 20% 不感知杠杆
- **文件**: `src/cryptobot/workflow/nodes/risk.py:356-360`
- **问题**: 5x 杠杆爆仓距离本身约 20%，硬门槛 20% 意味着几乎无缓冲。3x 杠杆爆仓距离 33%，20% 门槛又过于宽松
- **影响**: 高杠杆时缓冲不足，低杠杆时过度保守
- **修复**: 动态阈值 `min_liq_dist = max(15, 30 - (5 - leverage) * 5)`：3x→25%，5x→30%
- [x] 已修复 — 动态阈值 `max(15, 30 - (5-lev)*3)`

### P14. TRADE_SCHEMA 中 position_size_pct 无上界
- **文件**: `src/cryptobot/workflow/prompts.py` TRADE_SCHEMA
- **问题**: `position_size_pct` 的 schema 定义无 `maximum`，LLM 可能输出 150%
- **影响**: 虽然后续有风控，但 AI 建议过大仓位可能误导风控经理
- **修复**: 添加 `"minimum": 0.5, "maximum": 25`
- [x] 已修复 — schema 添加 minimum/maximum 约束

---

## MEDIUM（8 个 — 优化后可提升盈利）

### P15. 置信度定义模糊 — 各角色理解不一致
- **文件**: `src/cryptobot/workflow/prompts.py` 全部角色 prompt
- **问题**: 0-100 置信度没有明确定义。技术分析师的 70 和情绪分析师的 70 可能含义不同，导致置信度膨胀
- **影响**: 置信度 75% 实际胜率可能只有 50%
- **修复**: 在每个角色 prompt 中加入标准化定义，明确各分段含义和历史胜率对应关系
- [x] 已修复 — TRADER prompt 注入置信度量化标准 (85-100/70-84/55-69/40-54/0-39)

### P16. 分析师权重粒度太粗 — 只有 3 级
- **文件**: `src/cryptobot/evolution/analyst_weights.py:32-40`
- **问题**: 只有 high(≥70%)/normal/low(≤45%) 三级，70% 和 90% 准确率都是 "high"
- **影响**: 无法区分"还不错"和"非常准"的分析师
- **修复**: 改为 5 级: very_high(≥75%) / high(≥65%) / normal / low(≤45%) / very_low(≤35%)
- [x] 已修复 — 5 级权重: very_high/high/normal/low/very_low

### P17. Prompt 优化和置信度校准样本太少
- **文件**: `src/cryptobot/evolution/prompt_optimizer.py:60`, `src/cryptobot/journal/confidence_tuner.py:17`
- **问题**: 5 笔交易就触发 prompt 优化重建，15 笔就做置信度校准。正常统计波动可能误触发
- **影响**: 越优化越差（过拟合噪音）
- **修复**: prompt 优化最小样本 5→10，置信度校准 15→50，每桶最小 5→10
- [x] 已修复 — prompt 优化 5→10, 置信度校准 15→30, 每桶 5→8

### P18. 技术评分权重不随 regime 调整
- **文件**: `src/cryptobot/indicators/calculator.py:332-414`
- **问题**: RSI 超卖 +1.5、MACD 金叉 +2.0 等权重对所有市场状态一视同仁。高波动市 RSI 经常失效，趋势市 MACD 更可靠
- **影响**: 趋势市中信号不够强，高波动市中假信号多
- **修复**: 技术评分权重基于 regime 动态调整（volatile 时 RSI 权重×0.5，trending 时 EMA 权重×1.3）
- [x] 已修复 — `_generate_signals` 增加 regime 参数 + 权重乘数

### P19. 数据源矛盾无检测机制
- **文件**: 全局架构
- **问题**: 多空比看空 + Fear&Greed 中性 + 技术面看多时，分析师看到矛盾信号但无标准化处理。各自独立分析，trader 收到矛盾建议
- **影响**: 置信度分散，trader 难以做出高置信度决策
- **修复**: 增加数据一致性评分，多源矛盾时自动降低置信度上限
- [x] 已修复 — 分析师一致性评分，分歧时注入置信度上限建议

### P20. 信号不记录 regime 和 capital_tier
- **文件**: `src/cryptobot/journal/models.py`
- **问题**: `SignalRecord` 中没有 `regime_name`、`capital_tier`、`risk_review_changes` 字段
- **影响**: 无法事后分析"在什么市场状态、什么资金层级下表现好/差"
- **修复**: SignalRecord 增加 `regime_name: str | None`、`capital_tier: str | None`、`risk_review_changes: dict | None`
- [x] 已修复 — 3 个新字段 + execute/risk 节点注入

### P21. 持仓复审间隔 4h 太长
- **文件**: `config/settings.yaml:80`
- **问题**: 持仓每 4 小时复审一次，开仓后若市场快速反转，需等 4h 才能调整止损
- **影响**: 风险暴露时间过长
- **修复**: re_review_hours: 4→2
- [x] 已修复 — re_review_hours 4→2

### P22. EMA 排列判断过于简单 — 震荡市误判
- **文件**: `src/cryptobot/indicators/calculator.py:283-291`
- **问题**: 仅判断 `e7 > e25 > e99` → bullish，不考虑间距（convergence/divergence），震荡市 EMA 反复交叉产生假信号
- **影响**: 震荡市中技术分析师频繁切换多空方向
- **修复**: 增加 EMA 间距阈值（gap > 1.5% 才确认排列）+ MACD 交叉强度过滤
- [x] 已修复 — EMA 间距最低 0.1% 才确认排列

---

## 第二轮统计

| 等级 | 总数 | 已修复 |
|------|------|--------|
| CRITICAL | 5 | 5 |
| HIGH | 9 | 9 |
| MEDIUM | 8 | 8 |
| **合计** | **22** | **22** |

## 第二轮修复记录

### 第一批 — P1,P2,P3,P4,P14,P20 (基础设施 + 最高 ROI)
- Kelly 动态参数(P1) + 止损验证(P2) + RR 检查(P3) + 分批减仓(P4) + Schema 约束(P14) + 信号记录扩展(P20)

### 第二批 — P5,P6,P7,P8,P9,P10,P12,P13 (HIGH 级改进)
- 学习反馈环(P5) + 置信度阈值(P6) + micro 放宽(P7) + 入场价格(P8) + 尾随激活(P9) + 复审平仓(P10) + K 线新鲜度(P12) + 爆仓距离(P13)

### 第三批 — P11,P15,P16,P17,P18,P19,P21,P22 (MEDIUM 级优化)
- 实时情绪(P11) + 置信度定义(P15) + 权重粒度(P16) + 样本门槛(P17) + regime 权重(P18) + 矛盾检测(P19) + 复审间隔(P21) + EMA 间距(P22)

---

# 第三轮审计 — 盈利能力优化审查

> 审计日期: 2026-02-21 | 审查范围: 全部核心模块 (信号质量/入场出场/资金效率/数据利用率)
> 核心目标: 以"能否多赚钱、少亏钱"为标准，审查优化空间
> 发现: 32 个优化点 (2 极高 + 7 高 + 10 中高 + 9 中 + 4 低中)
> 状态标记: [ ] 待修复 | [x] 已修复 | [-] 不修复(接受现状)

---

## P0 — 极高影响，改动极小（2 个）

### O1. 交易决策未用强推理模型 — haiku/sonnet 都映射到同一模型
- **文件**: `config/settings.yaml:142-155`
- **问题**: `haiku` 和 `sonnet` 均映射到 `deepseek-chat`。系统设计中 trader/risk_manager 应用 sonnet（强模型），但实际落地两者完全相同。注释中已有 `deepseek-reasoner` 配置但被注释掉
- **影响**: 交易决策（最关键环节）没有得到更强模型加持，方向判断准确率和止损精度受限
- **修复**: 取消注释 `role_models`，为 trader/risk_manager 启用 `deepseek-reasoner`
- **工作量**: 改 1 行配置
- [x] 已修复

### O2. 11 个数据源在 prompt 中缺少分析指引 — AI 可能完全忽略
- **文件**: `src/cryptobot/workflow/prompts.py:34-130`（四个分析师 prompt）
- **问题**: 系统采集了 16+ 数据源并注入 AI，但 prompt 的分析框架中遗漏了大量数据源:
  - TECHNICAL_ANALYST: 缺 `orderbook`（订单簿深度）
  - ONCHAIN_ANALYST: 缺 `coinglass_liquidation`, `open_interest`, `options_sentiment`, `whale_activity`
  - SENTIMENT_ANALYST: 缺 `stablecoin_flows`, `macro_events`, `dxy`（美元指数）
  - FUNDAMENTAL_ANALYST: 缺 `dilution_risk`（代币稀释）, `defi_tvl`
- **影响**: 花大力气接入的数据源（订单簿、期权、巨鲸、稳定币流、DXY 等）AI 完全没在看
- **修复**: 为每个分析师 prompt 补全数据维度说明和分析框架
- **工作量**: 仅修改 prompt 文本
- [x] 已修复

---

## P1 — 高影响优化（7 个）

### O3. TRADER 缺少「入场类型」定价锚定指引
- **文件**: `src/cryptobot/workflow/prompts.py:168-204`
- **问题**: TRADER prompt 要求「设定合理入场范围」，但未区分「市价立即入场」和「限价等待回调」。realtime 监控有 120 分钟最大等待窗口（monitor.py:187），entry_range 离当前价太远则永远不被 promote；太近则丧失等待更好入场价的机会
- **影响**: 入场价差 1% 在 3x 杠杆下影响 3% 盈亏
- **修复**: (1) prompt 增加入场类型指引 (2) TRADE_SCHEMA 增加 `entry_type: "market"|"limit_wait"` (3) execute 节点据此决定写 signal.json 还是 pending
- **工作量**: 中
- [x] 已修复

### O4. 研究员只看分析师结论，看不到原始数据
- **文件**: `src/cryptobot/workflow/nodes/research.py:30-38`
- **问题**: 看多/看空研究员只收到 4 位分析师的结论性 JSON（direction/confidence/summary），完全看不到原始数据（价格、指标数值、资金费率等）。「多空辩论」变成「分析师结论的重新包装」
- **影响**: 研究员无法独立验证分析师判断、发现遗漏、构建更有说服力的论据
- **修复**: research 节点中将关键原始数据摘要（current_price, rsi, funding_rate, nearest_support/resistance 等）一并注入研究员 prompt
- **工作量**: 中
- [x] 已修复

### O5. 信号过期时间固定 4 小时，不区分市场状态
- **文件**: `src/cryptobot/signal/bridge.py:147-150`
- **问题**: 无论趋势/震荡/高波动，无论 BTC 还是 DOGE，信号有效期均固定 4 小时。趋势市好信号可能过早过期，震荡市过时信号仍有效
- **影响**: 趋势行情中好信号过早丢失（错过盈利），震荡市中过时信号导致亏损入场
- **修复**: (1) TRADE_SCHEMA 增加 `suggested_expiry_hours` (2) regime 兜底: trending=6h, ranging=2h, volatile=1.5h
- **工作量**: 中
- [x] 已修复

### O6. 分批止盈减仓量基于初始 stake_amount 而非当前剩余
- **文件**: `freqtrade_strategies/AgentSignalStrategy.py:367`
- **问题**: `reduce_amount = trade.stake_amount * reduce_pct / 100`，`trade.stake_amount` 是初始保证金。已执行第一次减仓后，该值不反映当前剩余仓位
- **影响**: 分批止盈精度直接影响利润锁定效率
- **修复**: 改为基于当前实际仓位: `current_value = trade.amount * current_rate`
- **工作量**: 低（1 行）
- [x] 已修复

### O7. Agent trailing_stop 激活门槛过高（5%）
- **文件**: `freqtrade_strategies/AgentSignalStrategy.py:244`
- **问题**: `current_profit > 0.05` — 需净利润超 5% 才激活。很多交易在 2-4% 利润时回撤变为亏损
- **影响**: trailing stop 在大量盈利交易中完全不生效
- **修复**: 降低到 2%，或让 AI 在信号中指定 `trailing_activation_pct`
- **工作量**: 低
- [x] 已修复

### O8. 入场确认逻辑 EMA7/EMA25 过于刚性
- **文件**: `src/cryptobot/realtime/monitor.py:115-144`
- **问题**: 做多要求 EMA7>EMA25（好的回调入场恰发生在 EMA7 暂时 <= EMA25 时）；RSI>=75 直接拒绝（强趋势中 RSI 可持续高位）；无成交量确认
- **影响**: 信号在最佳入场点被拒绝
- **修复**: (1) EMA 改为软信号 (2) RSI 阈值根据 regime 动态调整 (3) 增加放量突破确认
- **工作量**: 中
- [x] 已修复

### O9. 移动止盈三档固定参数与 AI 止盈规划冲突
- **文件**: `freqtrade_strategies/AgentSignalStrategy.py:261-268`
- **问题**: 固定三档（>20% 尾随 3%，>10% 尾随 5%，>5% 移至成本线）与 AI 分批止盈冲突: AI 计划 +15% 止第一批，但三档在 +10% 就用 5% 尾随，+12% 就平仓了
- **影响**: 过早退出盈利交易
- **修复**: 有 take_profit 列表时以 AI 规划为参考，仅超过所有 TP 后启用固定尾随
- **工作量**: 中
- [x] 已修复

---

## P2 — 中高影响优化（10 个）

### O10. perf_feedback 收集了但 screen/trade 节点未使用
- **文件**: `src/cryptobot/workflow/nodes/collect.py:196-208`
- **问题**: collect 节点计算了 `perf_feedback`（含 by_symbol 币种级胜率），存入 state，但 screen/trade 节点都没用
- **影响**: 反复在历史表现差的币种上亏损
- **修复**: (1) screen 对胜率 <30% 的币种减分 (2) trade 注入该币种历史表现到 prompt
- **工作量**: 低
- [x] 已修复

### O11. 盈亏比硬性门槛 1.5 在所有市场状态下一刀切
- **文件**: `src/cryptobot/workflow/nodes/risk.py:358-362`
- **问题**: 趋势市胜率高 RR 1.2 即可盈利但被 1.5 门槛拒绝；震荡市 regime_prompts 建议 RR>=2.0 但只检查 1.5
- **影响**: 趋势市少交易机会，震荡市多低质量交易
- **修复**: RR 门槛与 regime 联动: trending=1.2, ranging=2.0, volatile=2.0
- **工作量**: 低
- [x] 已修复

### O12. screen 筛选未考虑已持仓币种优先级
- **文件**: `src/cryptobot/workflow/nodes/screen.py:49-126`
- **问题**: screen 选出 3-5 个币种深度分析，但不知道当前持仓。已持仓币种未被选中则产生风险盲区
- **影响**: 已持仓币种的市场反转信号可能被漏掉
- **修复**: 对已持仓币种增加优先级加权（+5 分确保始终在分析列表中）
- **工作量**: 低
- [x] 已修复

### O13. 分析师权重仅作为文字建议，投票时等权
- **文件**: `src/cryptobot/workflow/nodes/trade.py:138-152`
- **问题**: 一致性评分中 4 位分析师等权投票。准确率 80% 的技术分析师和 40% 的情绪分析师投票权相同
- **影响**: 低质量分析师拉偏方向判断
- **修复**: (1) 一致性评分中使用加权投票 (2) prompt 中呈现加权方向结论
- **工作量**: 中
- [x] 已修复

### O14. 半凯利系数固定 0.5，不随信号质量调整
- **文件**: `src/cryptobot/risk/position_sizer.py:126`
- **问题**: 高确信度趋势交易（90+）和低确信度震荡交易（62）用相同 0.5 保守系数
- **影响**: 高确信度仓位偏小，低确信度仓位可能仍偏大
- **修复**: 根据 confidence 和 regime 动态调整: confidence>=85+trending=0.6, confidence<70+volatile=0.3
- **工作量**: 低
- [x] 已修复

### O15. Kelly 冷启动默认值导致公式输出为零
- **文件**: `src/cryptobot/risk/position_sizer.py:28-29`
- **问题**: 默认 win_rate=0.35, ratio=1.2 → f*=-0.192 → 半凯利=0。系统启动初期 Kelly 完全不生效
- **影响**: 前 10 笔交易仓位仅靠 2% 风险法
- **修复**: 冷启动默认值改为 win_rate=0.50, ratio=1.5（f*=0.167, 半凯利=8.3%）
- **工作量**: 低（改 2 个数值）
- [x] 已修复

### O16. trending 市最低置信度仅 50，几乎所有信号都能通过
- **文件**: `config/settings.yaml:123`, `src/cryptobot/workflow/prompts.py:192`
- **问题**: prompt 写「<60 建议不交易」，但 trending regime 设 min_confidence=50。50%≈抛硬币
- **影响**: 低质量信号通过硬性检查
- **修复**: trending 50→58, ranging 58→63, volatile 63→68
- **工作量**: 低
- [x] 已修复

### O17. 持仓复审链上数据严重不足
- **文件**: `src/cryptobot/workflow/re_review.py:88-91`
- **问题**: 复审链上分析师只收到 2/6 个数据源（缺 coinglass_liq, open_interest, options_sentiment, whale_activity）
- **影响**: 复审分析质量远低于首次分析
- **修复**: 补全 4 个数据源注入
- **工作量**: 低（增加 4 行）
- [x] 已修复

### O18. 持仓复审间隔不区分盈亏状态
- **文件**: `src/cryptobot/cli/scheduler.py:371`
- **问题**: 所有持仓统一每 2 小时复审。浮亏 8% 高危持仓和浮盈 15% 安全持仓用相同频率
- **影响**: 高危持仓可能从可救变为止损
- **修复**: pnl<-3% 或 pnl>10% 时触发单币种紧急复审
- **工作量**: 中
- [x] 已修复

### O19. Prompt 优化只分析亏损，不分析盈利优化空间
- **文件**: `src/cryptobot/evolution/prompt_optimizer.py:73-127`
- **问题**: `analyze_failures()` 只分析亏损。盈利交易也有优化空间: 止盈太早（MFE 远超止盈价）、高置信度仓位偏小
- **影响**: 只减少亏损但不增加盈利上限
- **修复**: 增加 `analyze_wins()` 函数
- **工作量**: 中
- [x] 已修复

---

## P3 — 中等影响（9 个）

### O20. 模型竞赛 consensus 在 2 模型场景下过于保守
- **文件**: `src/cryptobot/evolution/model_competition.py:148-150`
- **问题**: 2 模型意见不一致时强制 no_trade（1/2 不算超半数）
- **影响**: 竞赛模式交易频率大幅降低
- **修复**: 不一致时取更强模型结论；或增加第三模型打破平局
- **工作量**: 低
- [x] 已修复

### O21. confidence_tuner 动态调整幅度过小
- **文件**: `src/cryptobot/journal/confidence_tuner.py:74-85`
- **问题**: 每区间最多 ±5，总范围 [55,80]。严重过度自信时 +5 不够
- **影响**: 适应置信度偏差的速度太慢
- **修复**: 偏差 >30% 时 +10，>50% 时 +15；范围扩到 [50,85]
- **工作量**: 低
- [x] 已修复

### O22. Trader 没有收到该币种历史表现数据
- **文件**: `src/cryptobot/workflow/nodes/trade.py:154-174`
- **问题**: 全局绩效摘要有但无该币种的 `by_symbol` 数据
- **影响**: AI 无法在反复亏损的币种上更谨慎
- **修复**: trade prompt 中提取该币种历史表现
- **工作量**: 低
- [x] 已修复

### O23. screen 节点 CoinGecko API 串行调用，每币 2s 间隔
- **文件**: `src/cryptobot/workflow/nodes/screen.py:133-150`
- **问题**: 5 币种串行 + 2s 间隔 = 8-10s 延迟
- **影响**: 工作流延迟增加，数据时效降低
- **修复**: ThreadPoolExecutor(3) + Semaphore(2) 并行化
- **工作量**: 中
- [x] 已修复

### O24. 分析周期固定 30 分钟，4h K 线不变
- **文件**: `config/settings.yaml:78`
- **问题**: 30min 内 4h K 线几乎不变，浪费 LLM 调用
- **影响**: 浪费 API 成本
- **修复**: 根据 regime 动态调整: trending=90-120min, ranging=60min, volatile=30min
- **工作量**: 低
- [ ] 延迟 (需改 APScheduler 运行时调度架构)

### O25. exchange_reserve OI 数据仅支持 5/10 币种
- **文件**: `src/cryptobot/data/exchange_reserve.py:20`
- **问题**: ADAUSDT/DOGEUSDT/AVAXUSDT/LINKUSDT/SUIUSDT 无 OI 数据
- **影响**: 5 个币种链上分析缺 open_interest 维度
- **修复**: 验证 CoinGlass API 后扩展列表
- **工作量**: 低
- [x] 已修复

### O26. journal 同步缺少 exit_reason 和 duration_hours
- **文件**: `src/cryptobot/cli/scheduler.py:328-335`
- **问题**: 未填充退出原因和持仓时长，SignalRecord 模型已有这两个字段
- **影响**: 绩效分析不完整
- **修复**: 从 Freqtrade trade 数据提取
- **工作量**: 低
- [x] 已修复

### O27. Freqtrade 入场容差 50% 过大
- **文件**: `freqtrade_strategies/AgentSignalStrategy.py:139`
- **问题**: entry_range=[100,102] 时容差=1，允许 [99,103] 入场
- **影响**: 远离理想入场价建仓，恶化盈亏比
- **修复**: 容差 50%→15%
- **工作量**: 低
- [x] 已修复

### O28. RE_REVIEWER 缺少 regime 和绩效上下文
- **文件**: `src/cryptobot/workflow/re_review.py:130-149`
- **问题**: 复审员不知道市场 regime、币种历史、账户盈亏
- **影响**: 复审决策不贴合市场环境
- **修复**: 注入 regime 和绩效上下文到 prompt
- **工作量**: 低
- [x] 已修复

---

## P4 — 低影响 / 长期（4 个）

### O29. portfolio_context 缺少类别/相关性分组信息
- **文件**: `src/cryptobot/workflow/utils.py:19-78`
- **问题**: 未展示类别分布和相关性分组。pairs.yaml 有数据但未被利用
- **影响**: 无法评估组合集中度风险
- **修复**: 增加类别分布统计
- **工作量**: 低
- [x] 已修复

### O30. Prompt 版本缓存使用全局可变变量
- **文件**: `src/cryptobot/workflow/prompts.py:10-22`
- **问题**: 同一轮中 prompt 版本切换后仍用旧版本
- **影响**: 极少数边界情况
- **修复**: 改为每次读取或带 TTL 缓存
- **工作量**: 低
- [ ] 延迟 (已有 reset_prompt_version_cache 每轮重置)

### O31. 回测策略与实盘策略逻辑差异大
- **文件**: `freqtrade_strategies/AgentSignalStrategy.py:163-191`
- **问题**: 回测用简单规则，与实盘 AI 分析完全不同。回测结果不可信
- **影响**: Hyperopt 参数可能不适用于 AI 信号
- **修复**: 建立信号回放式回测框架
- **工作量**: 高
- [ ] 延迟 (独立子项目，数周级别)

### O32. 全局数据获取 _fetch_global 内部串行
- **文件**: `src/cryptobot/workflow/utils.py:81-116`
- **问题**: 5 个 HTTP 请求串行执行，可能耗时 5-10s
- **影响**: 数据采集延迟
- **修复**: 内部也使用 ThreadPoolExecutor 并行
- **工作量**: 低
- [x] 已修复

---

## 数据利用率审计表

| 数据源 | 采集模块 | 注入分析师 | prompt 有说明 | 状态 | 关联 |
|--------|---------|-----------|-------------|------|------|
| tech_indicators | calculator.py | technical | 有 | 完整 | - |
| multi_timeframe | multi_timeframe.py | technical | 有 | 完整 | - |
| volume_analysis | multi_timeframe.py | technical | 有 | 完整 | - |
| support_resistance | multi_timeframe.py | technical | 有 | 完整 | - |
| orderbook | orderbook.py | technical | 有 | 完整 | O2 ✅ |
| derivatives | crypto_specific.py | onchain | 有 | 完整 | - |
| liquidation | liquidation.py | onchain | 有 | 完整 | - |
| coinglass_liq | coinglass.py | onchain | 有 | 完整 | O2 ✅ |
| open_interest | exchange_reserve.py | onchain | 有 | 完整 | O2 ✅ |
| options_sentiment | options.py | onchain | 有 | 完整 | O2 ✅ |
| whale_activity | whale_tracker.py | onchain | 有 | 完整 | O2 ✅ |
| fear_greed | sentiment.py | sentiment | 有 | 完整 | - |
| market_overview | news.py | sentiment | 有 | 完整 | - |
| global_news | crypto_news.py | sentiment | 有 | 完整 | - |
| stablecoin_flows | stablecoin.py | sentiment | 有 | 完整 | O2 ✅ |
| macro_events | macro_calendar.py | sentiment+risk | 有 | 完整 | O2 ✅ |
| dxy | dxy.py | sentiment | 有 | 完整 | O2 ✅ |
| coin_info | news.py | fundamental | 有 | 完整 | - |
| btc_correlation | market_structure.py | fundamental | 有 | 完整 | - |
| coin_news | crypto_news.py | fundamental | 有 | 完整 | - |
| dilution_risk | token_unlocks.py | fundamental | 有 | 完整 | O2 ✅ |
| defi_tvl | defi_tvl.py | fundamental | 有 | 完整 | O2 ✅ |
| perf_feedback | analytics.py | screen+trade | 有 | 完整 | O10/O22 ✅ |

---

## 第三轮统计

| 优先级 | 数量 | 已修复 | 延迟 | 描述 |
|--------|------|--------|------|------|
| P0 极高影响 | 2 | 2 | 0 | O1-O2: 模型能力 + 数据利用 |
| P1 高影响 | 7 | 7 | 0 | O3-O9: 入场出场策略 |
| P2 中高影响 | 10 | 10 | 0 | O10-O19: 信号质量 + 资金效率 |
| P3 中等影响 | 9 | 8 | 1 | O20-O28: 精细化调优 (O24 延迟) |
| P4 低影响 | 4 | 2 | 2 | O29-O32: 长期优化 (O30/O31 延迟) |
| **合计** | **32** | **29** | **3** | |

---

# 第四轮审计 — 全模块深度审查（P13 完成后）

> 审计日期: 2026-02-23 | 审查范围: 全部模块 (6 个 Agent 并行审查)
> 测试状态: 1343 passed | 代码量: 24,500+ LOC
> 审查维度: bug/设计缺陷/逻辑错误/边界条件/统计正确性/不可变性/并发安全
> 发现: 54 个问题 (5 CRITICAL + 11 HIGH + 24 MEDIUM + 14 LOW)
> 状态标记: [ ] 待修复 | [x] 已修复 | [-] 不修复(接受风险)

---

## CRITICAL（5 个）

### R4-C1. 月度熔断 suspend 状态未持久化 — 永远无法恢复
- **文件**: `src/cryptobot/risk/monthly_circuit_breaker.py:157`
- **问题**: `resume_date = now + 7d` 每次调用都重新计算，从不读取已有 suspend 状态。每次检查都把恢复日期往后推 7 天，导致 suspend 永远不会到期。
- **影响**: 一旦触发 suspend，系统永久停止交易
- **修复**: 首次 suspend 时持久化 `{triggered_at, resume_date}` 到文件；后续检查读取已有状态，`now > resume_date` 时自动恢复
- [x] 已修复

### R4-C2. risk.py 多处直接修改 decision 字典（不可变性再次违反）
- **文件**: `src/cryptobot/workflow/nodes/risk.py:332, 529, 680, 691`
- **问题**: 第一轮 M3 仅修复了 :328 一处，但 P13 新增的月度熔断(332)、币种分级(529)、动态杠杆(680,691) 再次引入 `decision["leverage"] = ...` 直接修改。同一 decision 被多个硬规则反复修改，难以追踪最终值来源。
- **影响**: 多重规则叠加修改导致不可预期的杠杆值
- **修复**: 循环开头 `decision = {**decision}` 创建副本，所有修改作用于副本
- [x] 已修复

### R4-C3. model_competition 多线程内 task.pop() 修改传入字典
- **文件**: `src/cryptobot/evolution/model_competition.py:82`
- **问题**: `task.pop("_competition_model", None)` 在 ThreadPoolExecutor 线程内修改传入的 task 字典。虽然当前各线程操作不同 dict 对象，但违反不可变性原则，若外部持有同一引用则看到被修改的 dict。
- **修复**: 改用 `task.get()` 读取，不修改原字典
- [x] 已修复

### R4-C4. 资金费率套利 delta 中性实现不完整
- **文件**: `src/cryptobot/strategy/funding_arb.py:145-156`
- **问题**: 文档和注释声称 "现货做多 + 永续做空 = delta 中性"，但实际只开了 short 仓位，没有 long。当价格上涨时 short 亏损无法对冲。`spot_price` 参数传入后完全未使用（:121）。
- **影响**: 价格上涨时虚拟盘亏损，误导用户以为策略无方向风险
- **修复**: 方案 A — 同时开 long + short 实现真正 delta 中性; 方案 B — 修改文档为"单边费率收割"
- [x] 已修复

### R4-C5. 调度器全局变量无线程锁保护
- **文件**: `src/cryptobot/cli/scheduler.py:26-27, 31-59`
- **问题**: `_last_mtime` 和 `_last_config` 被多个定时任务线程读写，无 `threading.Lock` 保护。配置热更新与工作流执行可能并发触发竞态。
- **修复**: 添加 `_config_lock = threading.Lock()`
- [x] 已修复

---

## HIGH（11 个）

### R4-H1. 月度熔断：无交易月中断连续亏损计数
- **文件**: `src/cryptobot/risk/monthly_circuit_breaker.py:148-149`
- **问题**: `if m.trade_count == 0: break` — 连亏 2 月触发 suspend 暂停交易 → 暂停期无交易 → 下月"无交易月 break" → 连续计数归零 → 自动解除 suspend。与 R4-C1 联合形成 suspend 自我取消的循环。
- **修复**: 无交易月应 `continue` 跳过而非 `break` 中断
- [x] 已修复

### R4-H2. Welch t-test p-value 用正态近似代替 t 分布
- **文件**: `src/cryptobot/backtest/stats.py:83`
- **问题**: `p_value = math.erfc(abs(t_stat) / math.sqrt(2))` 对 t 统计量使用正态分布 CDF。小样本（n<30）时正态近似低估 p-value，导致统计检验假阳性率偏高。代码注释已说明是"正态近似"。
- **修复**: 实现 t 分布 CDF 近似或引入 scipy
- [x] 已修复

### R4-H3. 特征标准化用全量数据（ML 数据泄漏）
- **文件**: `src/cryptobot/features/pipeline.py:111-142`
- **问题**: `_normalize_z_score()` 在全量矩阵上计算 mean/std（包含测试集）。tree-based 模型影响较小，但影响 pipeline 严谨性。
- **修复**: 为 `normalize_features()` 添加 `fit_stats` 参数，允许传入训练集统计量
- [x] 已修复

### R4-H4. Walk-forward 训练窗口包含前期测试数据
- **文件**: `src/cryptobot/backtest/walk_forward.py:79-84`
- **问题**: Window 2 训练区间 [30-90] 包含 Window 1 测试区间 [60-90]。虽然测试区间不重叠，但训练使用了"已验证过"的数据，可能导致过拟合信号泄漏。
- **修复**: 明确 expanding vs sliding window 设计意图，若需严格隔离则 step_days >= train_days
- [x] 已修复

### R4-H5. 风控节点多处 except Exception: pass 静默吞错
- **文件**: `src/cryptobot/workflow/nodes/risk.py:230-241`
- **问题**: 多处 `except Exception: pass` 吞掉所有异常。风控 addon 加载失败（如 capital_prompts、regime_prompts）完全无日志，规则未注入但无人知晓。
- **修复**: 改为 `except Exception as e: logger.warning(...)`
- [x] 已修复

### R4-H6. 月度熔断逻辑被执行了两次（双重降杠杆）
- **文件**: `src/cryptobot/workflow/nodes/risk.py:308-334 + 687-710`
- **问题**: 月度熔断检查分布在两个位置，P13 实施时第一处已有，第二处新增。若信号未在第一处被 `continue` 拒绝，会在第二处被二次降杠杆（5x → 3x → 2x）。
- **修复**: 合并为单一检查点，或第二处检查是否已处理
- [x] 已修复

### R4-H7. symbol_profile C 级条件用 OR 过于宽松
- **文件**: `src/cryptobot/risk/symbol_profile.py:49`
- **问题**: `if win_rate > 0.35 or avg_pnl > -1.0: return "C"` — 胜率 10% 但均亏 -0.5% 的币种也被评为 C 级。几乎不可能有币种被评为 D 级。
- **修复**: 改为 `and` 或调整阈值
- [x] 已修复

### R4-H8. 实时监控 promote 信号不具原子性
- **文件**: `src/cryptobot/realtime/monitor.py:164-181`
- **问题**: 先写 signal.json 再删 pending，两步之间若进程崩溃导致信号重复。
- **修复**: promote 前检查 signal.json 中是否已有相同 symbol 活跃信号
- [x] 已修复

### R4-H9. Web 本地访问检测可被反向代理绕过
- **文件**: `src/cryptobot/web/app.py:31-36`
- **问题**: `_is_local()` 只检查 `request.client.host`。nginx/caddy 反向代理后 client.host 是代理 IP（127.0.0.1），所有请求跳过 token 认证。
- **修复**: 存在 `X-Forwarded-For` 头时不判定为本地访问
- [x] 已修复

### R4-H10. 置信度校准分桶边界不清晰
- **文件**: `src/cryptobot/journal/analytics.py:130-135`
- **问题**: `"60-70": {min:60, max:70}` 和 `"70-80": {min:70, max:80}`，confidence=70 归属取决于 `<` vs `<=`。
- **修复**: 统一使用 `[min, max)` 左闭右开
- [x] 已修复

### R4-H11. virtual_portfolio 浮点精度累积误差
- **文件**: `src/cryptobot/strategy/virtual_portfolio.py:56, 116`
- **问题**: 只在最终结果 `round(new_balance, 4)`，中间无精度控制。多次 open/close 后误差累积。
- **修复**: 中间步骤也做精度控制或使用 Decimal
- [x] 已修复

---

## MEDIUM（24 个）

### R4-M1. 月份计算用 i×28 天近似
- **文件**: `monthly_circuit_breaker.py:62`
- **问题**: `now.replace(day=1) - timedelta(days=i * 28)` 不精确。有 `set()` 去重兜底，但意图不清晰。
- [x] 已修复

### R4-M2. pnl_pct 始终为 0.0
- **文件**: `monthly_circuit_breaker.py:79`
- **问题**: "缺少月初余额，用 0.0 占位"，下游使用者可能误读。
- [x] 已修复

### R4-M3. lgb_scorer K-fold 未打乱顺序
- **文件**: `ml/lgb_scorer.py:176-194`
- **问题**: 按连续索引切分。若特征有时间序列性，CV 过于乐观。
- [x] 已修复

### R4-M4. walk_forward/equity_tracker Sharpe 年化因子假设日频交易
- **文件**: `walk_forward.py:231-244`, `equity_tracker.py:131-142`
- **问题**: `sqrt(365)` 假设每天 1 笔交易，实际交易密度远低。Sharpe 被系统性高估。
- [x] 已修复

### R4-M5. bootstrap profit_factor inf 处理不当
- **文件**: `backtest/bootstrap.py:190-210`
- **问题**: 超 50% 样本为 inf 时返回点估计作为 CI，摧毁置信区间意义。
- [x] 已修复

### R4-M6. sentiment 多空比 ×100 可能导致百分比 >100
- **文件**: `data/sentiment.py:155-156`
- **问题**: Binance API 返回 0-1 比例，再乘 100 超预期。
- [x] 已修复

### R4-M7. 调度器任务失败无重试无通知
- **文件**: `cli/scheduler.py:94-109`
- **问题**: 工作流失败只记日志，用户可能数小时不知道系统中断。
- [x] 已修复

### R4-M8. strategy_advisor 时间戳字符串比较（时区不一致风险）
- **文件**: `evolution/strategy_advisor.py:153, 160-161`
- **问题**: `r.timestamp < created_at` 字符串比较，UTC 带 Z vs 无时区时出错。
- [x] 已修复

### R4-M9. strategy_advisor 规则续期无 renewed_at 字段
- **文件**: `evolution/strategy_advisor.py:113-117`
- **问题**: 多次续期后 `created_at` 仍为初始时间，无法追踪续期历史。
- [x] 已修复

### R4-M10. regime_evaluator 样本量阈值 4 太低
- **文件**: `journal/regime_evaluator.py:145`
- **问题**: Welch t-test 需至少 10-30 样本。4 个样本统计检验几乎无意义。
- [x] 已修复

### R4-M11. prompt_optimizer 退化阈值不考虑统计波动
- **文件**: `evolution/prompt_optimizer.py:57-61`
- **问题**: 30 笔交易胜率波动 ~9%，`wr_short < wr_long * 0.8` 可能误触发。
- [x] 已修复

### R4-M12. model_competition 新模型默认 win_rate=0.5
- **文件**: `evolution/model_competition.py:173-186`
- **问题**: 未出现在历史中的模型默认 0.5，既不鼓励探索也不惩罚未知。
- [x] 已修复

### R4-M13. factor_analysis 返回空因子名
- **文件**: `features/factor_analysis.py:95-120`
- **问题**: 样本不足时 `factor_name=""`，后续需替换，增加维护复杂性。
- [x] 已修复

### R4-M14. dxy 缓存过期命名误导
- **文件**: `data/dxy.py:16`
- **问题**: `STALE_CACHE_TTL` 实际语义是"最大缓存年龄"。
- [x] 已修复

### R4-M15. signal bridge ISO 格式异常处理粒度不足
- **文件**: `signal/bridge.py:57`
- **问题**: `fromisoformat()` 失败时整个信号列表返回空。
- [x] 已修复

### R4-M16. journal storage 读写锁粒度
- **文件**: `journal/storage.py:22-25`
- **问题**: `_load_data()` 可能在锁外被调用。
- [x] 已修复

### R4-M17. Web API 批量更新错误处理不完善
- **文件**: `web/routes/api.py:172-189`
- **问题**: 第一个字段验证失败即返回 400，后续字段不处理。
- [x] 已修复

### R4-M18. Web Auth 缺少 /static 路由豁免
- **文件**: `web/app.py:18-19`
- **问题**: `_PUBLIC_PREFIXES` 未含 `/static`，静态资源可能被拦截。
- [x] 已修复

### R4-M19. check_arb_positions 平仓条件不考虑 PnL
- **文件**: `strategy/funding_arb.py:159-186`
- **问题**: 只检查费率阈值，不检查浮亏。价格大涨时 short 亏损超 funding 收入。
- [x] 已修复

### R4-M20. multi_timeframe 异常捕获不一致
- **文件**: `indicators/multi_timeframe.py:91-114`
- **问题**: 1h/4h 只捕获 FileNotFoundError，feather 损坏时 EOFError 未处理。
- [x] 已修复

### R4-M21. calculator K 线缓存 TTL 30min 偏长
- **文件**: `indicators/calculator.py:37-41`
- **问题**: 1h K 线缓存 30 分钟，实时监控可能用半小时前数据做决策。
- [x] 已修复

### R4-M22. historical_replay 日期采样逻辑
- **文件**: `backtest/historical_replay.py:506-523`
- **问题**: `range(total_hours, 0, -interval_hours)` 可能产生超预期采样点数。
- [x] 已修复

### R4-M23. grid_trading 网格触发浮点精度问题
- **文件**: `strategy/grid_trading.py:152, 174`
- **问题**: `current_price <= level.price` 直接浮点比较，边界误触发。
- [x] 已修复

### R4-M24. Freqtrade 分批止盈计算可读性差
- **文件**: `freqtrade_strategies/AgentSignalStrategy.py:415`
- **问题**: `trade.amount * current_rate / trade.leverage * reduce_pct / 100` 虽然近似正确（≈ stake_amount * reduce_pct / 100），但单位混乱、可读性差，且与 PnL 变化后的实际 stake 有偏差。
- [x] 已修复

---

## LOW（14 个）

### R4-L1. regime_smoother 初始状态硬编码 "ranging"
- **文件**: `regime_smoother.py:21-34`
- [x] 已修复

### R4-L2. notify Markdown 转义不完整
- **文件**: `notify.py:162-166`
- [x] 已修复

### R4-L3. hurst 数据长度检查冗余
- **文件**: `indicators/hurst.py:26-46`
- [x] 已修复

### R4-L4. prompt_manager 版本号解析简陋
- **文件**: `evolution/prompt_manager.py:82-91`
- [x] 已修复

### R4-L5. config 缓存异常后不强制重载
- **文件**: `config.py:56-62`
- [x] 已修复

### R4-L6. cost_model 资金费率线性假设
- **文件**: `backtest/cost_model.py:48-50`
- [x] 已修复

### R4-L7. archive 清理用字符串比较日期
- **文件**: `cli/archive.py:183-192`
- [x] 已修复

### R4-L8. init_cmd .env 追加可能重复
- **文件**: `cli/init_cmd.py:61-64`
- [x] 已修复

### R4-L9. doctor Freqtrade ping 无超时
- **文件**: `cli/doctor.py:103-111`
- [x] 已修复

### R4-L10. archive 大数据集查询无索引
- **文件**: `archive/reader.py:64-111`
- [x] 已修复

### R4-L11. grid_trading auto_detect_range percentile 计算简陋
- **文件**: `strategy/grid_trading.py:93-126`
- [x] 已修复

### R4-L12. Kelly source 标签不区分
- **文件**: `risk/position_sizer.py:107, 117`
- [x] 已修复

### R4-L13. execution_optimizer 结算时间条件冗余
- **文件**: `risk/execution_optimizer.py:96-98`
- [x] 已修复

### R4-L14. mean_reversion int() 截断
- **文件**: `strategy/mean_reversion.py:119, 128`
- [x] 已修复

---

## 第四轮修复优先级建议

### Phase 1 — 立即修复（影响交易安全）
1. R4-C1 月度熔断持久化 + R4-H1 无交易月计数 + R4-H6 双重降杠杆（一组关联问题）
2. R4-C4 资金费率套利 delta 中性
3. R4-C5 调度器线程安全
4. R4-H8 信号 promote 原子性

### Phase 2 — 近期修复（影响数据质量和安全）
1. R4-C2 risk.py 不可变性
2. R4-C3 model_competition 不可变性
3. R4-H5 except pass 吞错误
4. R4-H7 C 级判定过宽
5. R4-H9 Web 代理绕过
6. R4-H2 Welch t-test 精度

### Phase 3 — 计划修复（影响回测/ML 质量）
1. R4-H3 特征标准化泄漏
2. R4-H4 walk_forward 窗口重叠
3. R4-M3 K-fold 打乱
4. R4-M4 Sharpe 年化因子
5. R4-M5 bootstrap inf 处理
6. R4-M10 样本量阈值

### Phase 4 — 逐步改进
- 所有 MEDIUM 和 LOW 级别问题

---

## 第四轮统计

| 等级 | 总数 | 已修复 | 待修复 |
|------|------|--------|--------|
| CRITICAL | 5 | 5 | 0 |
| HIGH | 11 | 11 | 0 |
| MEDIUM | 24 | 24 | 0 |
| LOW | 14 | 14 | 0 |
| **合计** | **54** | **54** | **0** |
