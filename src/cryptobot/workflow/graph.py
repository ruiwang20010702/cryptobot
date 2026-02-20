"""LangGraph 自动化分析工作流

状态图: collect_data → screen → analyze → research → trade → risk_review → execute
"""

import json
import logging
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from typing import TypedDict

from langgraph.graph import StateGraph, END
from rich.console import Console

from cryptobot.workflow.llm import call_claude_parallel
from cryptobot.workflow.prompts import (
    TECHNICAL_ANALYST,
    ONCHAIN_ANALYST,
    SENTIMENT_ANALYST,
    FUNDAMENTAL_ANALYST,
    BULL_RESEARCHER,
    BEAR_RESEARCHER,
    TRADER,
    RISK_MANAGER,
    ANALYST_SCHEMA,
    BULL_SCHEMA,
    BEAR_SCHEMA,
    TRADE_SCHEMA,
    RISK_SCHEMA,
)

logger = logging.getLogger(__name__)
_console = Console()

STEPS_TOTAL = 7


def _stage(step: int, msg: str):
    """打印工作流阶段进度"""
    _console.print(f"[cyan][{step}/{STEPS_TOTAL}][/cyan] {msg}")


# ─── State ───────────────────────────────────────────────────────────────

class WorkflowState(TypedDict, total=False):
    market_data: dict        # collect_data: {symbol: {tech, crypto, multi_tf, volume_analysis, support_resistance, liquidation, btc_correlation, coin_info, coin_news}}
    market_overview: dict    # collect_data: 全局市场概览
    fear_greed: dict         # collect_data: 恐惧贪婪指数
    global_news: dict        # collect_data: 全局新闻情绪
    market_regime: dict      # collect_data: {regime, confidence, params, description}
    screened_symbols: list   # screen: 筛选出的 3-5 个币种
    analyses: dict           # analyze: {symbol: {tech, onchain, sentiment, fundamental}}
    research: dict           # research: {symbol: {bull, bear}}
    decisions: list          # trade: [{symbol, action, ...}]
    approved_signals: list   # risk_review: 通过风控的信号
    executed: list           # execute: 写入 signal.json 的结果
    errors: list             # 各节点错误收集


# ─── Node: collect_data ──────────────────────────────────────────────────

def collect_data(state: WorkflowState) -> dict:
    """采集所有币种的市场数据（纯 Python，不调 LLM）"""
    from cryptobot.config import get_all_symbols
    from cryptobot.indicators.calculator import calc_all_indicators
    from cryptobot.indicators.crypto_specific import calc_crypto_indicators
    from cryptobot.indicators.multi_timeframe import calc_multi_timeframe, calc_volume_analysis, calc_support_resistance
    from cryptobot.indicators.market_structure import calc_btc_correlation
    from cryptobot.data.sentiment import get_fear_greed_index
    from cryptobot.data.news import get_market_overview
    from cryptobot.data.liquidation import get_force_orders
    from cryptobot.data.crypto_news import get_crypto_news

    errors = list(state.get("errors", []))
    market_data = {}
    symbols = get_all_symbols()
    _stage(1, f"数据采集 — {len(symbols)} 个币种 (扩展数据)")
    t0 = time.time()

    # ── 全局数据 (并行) ──
    fear_greed = {"current_value": 50, "current_classification": "Neutral"}
    market_overview = {}
    global_news = {}

    def _fetch_global():
        _fg, _mo, _gn = None, None, None
        _errs = []
        try:
            _fg = get_fear_greed_index()
        except Exception as e:
            _errs.append(f"fear_greed: {e}")
        try:
            _mo = get_market_overview()
        except Exception as e:
            _errs.append(f"market_overview: {e}")
        try:
            currencies = [s.replace("USDT", "") for s in symbols]
            _gn = get_crypto_news(currencies)
        except Exception as e:
            _errs.append(f"global_news: {e}")
        return _fg, _mo, _gn, _errs

    # ── 每币种采集 (并行) ──
    def _fetch_symbol(symbol):
        data = {"symbol": symbol}
        errs = []
        # 已有: 4h 技术指标
        try:
            data["tech"] = calc_all_indicators(symbol, "4h")
        except Exception as e:
            logger.warning("技术指标失败 %s: %s", symbol, e)
            data["tech"] = None
            errs.append(f"tech_{symbol}: {e}")
        # 已有: 链上衍生品指标
        try:
            data["crypto"] = calc_crypto_indicators(symbol)
        except Exception as e:
            logger.warning("链上指标失败 %s: %s", symbol, e)
            data["crypto"] = None
            errs.append(f"crypto_{symbol}: {e}")
        # 新增: 多时间框架共振
        try:
            data["multi_tf"] = calc_multi_timeframe(symbol)
        except Exception as e:
            logger.warning("多时间框架失败 %s: %s", symbol, e)
            data["multi_tf"] = None
            errs.append(f"multi_tf_{symbol}: {e}")
        # 新增: 量价分析
        try:
            data["volume_analysis"] = calc_volume_analysis(symbol)
        except Exception as e:
            logger.warning("量价分析失败 %s: %s", symbol, e)
            data["volume_analysis"] = None
            errs.append(f"volume_{symbol}: {e}")
        # 新增: 支撑阻力位
        try:
            data["support_resistance"] = calc_support_resistance(symbol)
        except Exception as e:
            logger.warning("支撑阻力失败 %s: %s", symbol, e)
            data["support_resistance"] = None
            errs.append(f"sr_{symbol}: {e}")
        # 新增: 强平数据
        try:
            data["liquidation"] = get_force_orders(symbol)
        except Exception as e:
            logger.warning("强平数据失败 %s: %s", symbol, e)
            data["liquidation"] = None
            errs.append(f"liq_{symbol}: {e}")
        return symbol, data, errs

    # 并行: 全局 + 每币种
    with ThreadPoolExecutor(max_workers=6) as executor:
        global_future = executor.submit(_fetch_global)
        symbol_futures = {executor.submit(_fetch_symbol, s): s for s in symbols}

        # 收集全局数据
        fg, mo, gn, g_errs = global_future.result()
        if fg:
            fear_greed = fg
        if mo:
            market_overview = mo
        if gn:
            global_news = gn
        errors.extend(g_errs)

        # 收集每币种数据
        for future in as_completed(symbol_futures):
            symbol, data, errs = future.result()
            market_data[symbol] = data
            errors.extend(errs)

    # BTC 联动性 (需要 BTC 的 tech 数据先就绪)
    btc_tech = market_data.get("BTCUSDT", {}).get("tech")
    for symbol in symbols:
        try:
            market_data[symbol]["btc_correlation"] = calc_btc_correlation(
                symbol, btc_tech=btc_tech, market_overview=market_overview
            )
        except Exception as e:
            logger.warning("BTC 联动失败 %s: %s", symbol, e)
            market_data[symbol]["btc_correlation"] = None
            errors.append(f"btc_corr_{symbol}: {e}")

    ok = sum(1 for d in market_data.values() if d.get("tech"))
    fail_rate = (len(symbols) - ok) / len(symbols) if symbols else 1

    _console.print(f"    完成: {ok}/{len(symbols)} 有技术数据, "
                    f"恐惧贪婪={fear_greed.get('current_value', '?')}, "
                    f"耗时 {time.time() - t0:.0f}s")

    if fail_rate > 0.5:
        logger.error("数据采集失败率 %.0f%% > 50%%, 跳过本轮分析", fail_rate * 100)
        _console.print(f"    [red]数据质量不足 ({ok}/{len(symbols)})，跳过本轮[/red]")
        return {
            "market_data": {},
            "screened_symbols": [],
            "errors": errors,
        }

    # 市场状态检测
    regime = _detect_market_regime(market_data, fear_greed)
    _console.print(f"    市场状态: {regime['regime']} (置信度 {regime['confidence']}%)")

    return {
        "market_data": market_data,
        "market_overview": market_overview,
        "fear_greed": fear_greed,
        "global_news": global_news,
        "market_regime": regime,
        "errors": errors,
    }


def collect_data_for_symbols(symbols: list[str]) -> dict:
    """仅为指定币种采集数据（持仓复审专用）

    Returns:
        {market_data: {...}, fear_greed: {...}, market_overview: {...}, global_news: {...}}
    """
    from cryptobot.indicators.calculator import calc_all_indicators
    from cryptobot.indicators.crypto_specific import calc_crypto_indicators
    from cryptobot.indicators.multi_timeframe import calc_multi_timeframe, calc_volume_analysis, calc_support_resistance
    from cryptobot.indicators.market_structure import calc_btc_correlation
    from cryptobot.data.sentiment import get_fear_greed_index
    from cryptobot.data.news import get_market_overview
    from cryptobot.data.liquidation import get_force_orders
    from cryptobot.data.crypto_news import get_crypto_news

    _console.print(f"[cyan]采集 {len(symbols)} 个持仓币种数据...[/cyan]")
    market_data = {}
    fear_greed = {"current_value": 50, "current_classification": "Neutral"}
    market_overview = {}
    global_news = {}
    errors = []

    def _fetch_global():
        _fg, _mo, _gn = None, None, None
        _errs = []
        try:
            _fg = get_fear_greed_index()
        except Exception as e:
            _errs.append(f"fear_greed: {e}")
        try:
            _mo = get_market_overview()
        except Exception as e:
            _errs.append(f"market_overview: {e}")
        try:
            currencies = [s.replace("USDT", "") for s in symbols]
            _gn = get_crypto_news(currencies)
        except Exception as e:
            _errs.append(f"global_news: {e}")
        return _fg, _mo, _gn, _errs

    def _fetch_symbol(symbol):
        data = {"symbol": symbol}
        errs = []
        try:
            data["tech"] = calc_all_indicators(symbol, "4h")
        except Exception as e:
            data["tech"] = None
            errs.append(f"tech_{symbol}: {e}")
        try:
            data["crypto"] = calc_crypto_indicators(symbol)
        except Exception as e:
            data["crypto"] = None
            errs.append(f"crypto_{symbol}: {e}")
        try:
            data["multi_tf"] = calc_multi_timeframe(symbol)
        except Exception as e:
            data["multi_tf"] = None
            errs.append(f"multi_tf_{symbol}: {e}")
        try:
            data["volume_analysis"] = calc_volume_analysis(symbol)
        except Exception as e:
            data["volume_analysis"] = None
            errs.append(f"volume_{symbol}: {e}")
        try:
            data["support_resistance"] = calc_support_resistance(symbol)
        except Exception as e:
            data["support_resistance"] = None
            errs.append(f"sr_{symbol}: {e}")
        try:
            data["liquidation"] = get_force_orders(symbol)
        except Exception as e:
            data["liquidation"] = None
            errs.append(f"liq_{symbol}: {e}")
        return symbol, data, errs

    with ThreadPoolExecutor(max_workers=6) as executor:
        global_future = executor.submit(_fetch_global)
        symbol_futures = {executor.submit(_fetch_symbol, s): s for s in symbols}

        fg, mo, gn, g_errs = global_future.result()
        if fg:
            fear_greed = fg
        if mo:
            market_overview = mo
        if gn:
            global_news = gn
        errors.extend(g_errs)

        for future in as_completed(symbol_futures):
            symbol, data, errs = future.result()
            market_data[symbol] = data
            errors.extend(errs)

    # BTC 联动性
    btc_tech = market_data.get("BTCUSDT", {}).get("tech")
    for symbol in symbols:
        if symbol in market_data:
            try:
                market_data[symbol]["btc_correlation"] = calc_btc_correlation(
                    symbol, btc_tech=btc_tech, market_overview=market_overview
                )
            except Exception as e:
                market_data[symbol]["btc_correlation"] = None
                errors.append(f"btc_corr_{symbol}: {e}")

    if errors:
        for err in errors:
            logger.warning("数据采集: %s", err)

    return {
        "market_data": market_data,
        "fear_greed": fear_greed,
        "market_overview": market_overview,
        "global_news": global_news,
    }


# ─── 市场状态检测 ─────────────────────────────────────────────────────────

# 各状态的策略参数默认值
_REGIME_PARAMS = {
    "trending": {
        "min_confidence": 55,
        "max_leverage": 5,
        "trailing_stop": True,
        "description": "趋势市: EMA 多头/空头排列，ADX>25。适合顺势交易，可适度加仓。",
    },
    "ranging": {
        "min_confidence": 65,
        "max_leverage": 3,
        "trailing_stop": False,
        "description": "震荡市: ADX<20，布林带收窄。区间交易为主，轻仓博反弹。",
    },
    "volatile": {
        "min_confidence": 70,
        "max_leverage": 2,
        "trailing_stop": True,
        "description": "剧烈波动: ATR 显著放大，恐惧贪婪极端。降低杠杆，严格止损。",
    },
}


def _detect_market_regime(market_data: dict, fear_greed: dict) -> dict:
    """基于 BTC 技术指标 + 恐惧贪婪判断市场状态

    Returns:
        {regime: "trending"|"ranging"|"volatile",
         confidence: 0-100,
         params: {...},
         description: "..."}
    """
    btc = market_data.get("BTCUSDT", {})
    tech = btc.get("tech") or {}

    adx = (tech.get("trend") or {}).get("adx")
    atr_pct = (tech.get("volatility") or {}).get("atr_pct", 0)
    bb_width = (tech.get("volatility") or {}).get("bb_width", 0)
    fg_val = fear_greed.get("current_value", 50)

    # 评分
    volatile_score = 0
    trending_score = 0
    ranging_score = 0

    # ATR 波动率
    if atr_pct > 4:
        volatile_score += 3
    elif atr_pct > 2.5:
        volatile_score += 1
    elif atr_pct < 1.5:
        ranging_score += 2

    # 布林带宽度
    if bb_width and bb_width > 8:
        volatile_score += 2
    elif bb_width and bb_width < 3:
        ranging_score += 2

    # ADX 趋势强度
    if adx and adx > 30:
        trending_score += 3
    elif adx and adx > 25:
        trending_score += 2
    elif adx and adx < 20:
        ranging_score += 2

    # 恐惧贪婪极端值
    if fg_val < 20 or fg_val > 80:
        volatile_score += 2
    elif 40 <= fg_val <= 60:
        ranging_score += 1

    # 选择得分最高的状态
    scores = {
        "trending": trending_score,
        "ranging": ranging_score,
        "volatile": volatile_score,
    }
    regime = max(scores, key=scores.get)
    max_score = scores[regime]
    total = sum(scores.values()) or 1
    confidence = round(max_score / total * 100)

    params = _REGIME_PARAMS[regime]
    return {
        "regime": regime,
        "confidence": confidence,
        "params": {k: v for k, v in params.items() if k != "description"},
        "description": params["description"],
    }


# ─── 数据质量评分 ─────────────────────────────────────────────────────────

def _data_quality_score(data: dict) -> int:
    """评估单币种数据完整性 (0-100)

    权重: tech=30, crypto=15, multi_tf=15, volume_analysis=10,
          support_resistance=10, liquidation=10, btc_correlation=10
    """
    weights = {
        "tech": 30,
        "crypto": 15,
        "multi_tf": 15,
        "volume_analysis": 10,
        "support_resistance": 10,
        "liquidation": 10,
        "btc_correlation": 10,
    }
    score = 0
    for key, weight in weights.items():
        val = data.get(key)
        if val is not None and not (isinstance(val, dict) and "error" in val):
            score += weight
    return score


# ─── Node: screen ────────────────────────────────────────────────────────

def screen(state: WorkflowState) -> dict:
    """规则筛选，选出 3-5 个最值得分析的币种，筛选后延迟加载 CoinGecko/CryptoPanic"""
    from cryptobot.config import get_pair_config
    from cryptobot.data.news import get_coin_info
    from cryptobot.data.crypto_news import get_coin_specific_news

    _stage(2, "规则筛选")
    market_data = state.get("market_data", {})
    errors = list(state.get("errors", []))
    scores = []

    for symbol, data in market_data.items():
        score = 0
        tech = data.get("tech")
        crypto = data.get("crypto")

        # 数据质量门槛: < 40 跳过
        quality = _data_quality_score(data)
        if quality < 40:
            logger.info("跳过 %s: 数据质量 %d/100 < 40", symbol, quality)
            continue

        if not tech:
            continue

        signals = tech.get("signals", {})
        tech_score = abs(signals.get("technical_score", 0))

        # 技术评分越极端越值得分析
        score += tech_score * 2

        # RSI 极端值
        rsi = tech.get("momentum", {}).get("rsi_14")
        if rsi is not None:
            if rsi > 70 or rsi < 30:
                score += 3
            elif rsi > 60 or rsi < 40:
                score += 1

        # MACD 交叉
        macd_cross = tech.get("trend", {}).get("macd_cross", "none")
        if macd_cross in ("golden_cross", "death_cross"):
            score += 3

        # ATR 波动率 — 高波动更有交易机会
        atr_pct = tech.get("volatility", {}).get("atr_pct", 0)
        if atr_pct > 3:
            score += 2
        elif atr_pct > 1.5:
            score += 1

        # 链上数据综合评分
        if crypto:
            composite = crypto.get("composite", {})
            score += abs(composite.get("score", 0))

        # 新增: 多时间框架共振
        multi_tf = data.get("multi_tf")
        if multi_tf and multi_tf.get("aligned_count", 0) >= 2:
            score += 2

        # 新增: 清算强度
        liq = data.get("liquidation")
        if liq and liq.get("intensity") in ("high", "extreme"):
            score += 2

        # 新增: 量价背离
        vol = data.get("volume_analysis")
        if vol and vol.get("obv_divergence") in ("bullish_divergence", "bearish_divergence"):
            score += 2

        # 配对配置优先级 (BTC/ETH 权重高)
        pair_cfg = get_pair_config(symbol)
        if pair_cfg and pair_cfg.get("category") in ("store_of_value", "smart_contract"):
            score += 1

        scores.append((symbol, round(score, 1)))

    # 按分数降序，取前 5
    scores.sort(key=lambda x: x[1], reverse=True)
    screened = [s[0] for s in scores[:5]]

    ranked = [(s, sc) for s, sc in scores[:5]]
    _console.print(f"    筛选结果: {', '.join(f'{s}({sc})' for s, sc in ranked)}")
    logger.info("筛选结果: %s", ranked)

    # 延迟加载: 只对筛选出的 5 个币种获取 CoinGecko + CryptoPanic 数据
    for i, symbol in enumerate(screened):
        base = symbol.replace("USDT", "")
        # CoinGecko coin_info (间隔 2s 避免 429)
        if i > 0:
            time.sleep(2)
        try:
            market_data[symbol]["coin_info"] = get_coin_info(base)
        except Exception as e:
            logger.warning("币种信息失败 %s: %s", symbol, e)
            market_data[symbol]["coin_info"] = None
            errors.append(f"coin_info_{symbol}: {e}")
        # CryptoPanic 币种新闻
        try:
            market_data[symbol]["coin_news"] = get_coin_specific_news(symbol)
        except Exception as e:
            logger.warning("币种新闻失败 %s: %s", symbol, e)
            market_data[symbol]["coin_news"] = None
            errors.append(f"coin_news_{symbol}: {e}")

    return {"screened_symbols": screened, "market_data": market_data, "errors": errors}


# ─── Node: analyze (所有币种 × 4 分析师 全并行) ──────────────────────────

def analyze(state: WorkflowState) -> dict:
    """所有币种的 4 位分析师并行（5币 × 4 = 20 个 haiku，5 并发）"""
    screened = state.get("screened_symbols", [])
    n_tasks = len(screened) * 4
    _stage(3, f"分析师分析 — {n_tasks} 个 haiku ({len(screened)} 币 x 4 分析师)")
    t0 = time.time()
    market_data = state.get("market_data", {})
    fear_greed = state.get("fear_greed", {})
    market_overview = state.get("market_overview", {})
    global_news = state.get("global_news", {})
    errors = list(state.get("errors", []))

    # 打平所有任务: [(symbol, analyst_type, task_dict), ...]
    all_tasks = []
    task_index = []  # 记录每个任务对应的 (symbol, analyst_type)

    for symbol in screened:
        data = market_data.get(symbol, {})
        tech = data.get("tech", {})
        crypto = data.get("crypto", {})
        coin_info = data.get("coin_info", {})
        multi_tf = data.get("multi_tf", {})
        volume_analysis = data.get("volume_analysis", {})
        support_resistance = data.get("support_resistance", {})
        liquidation = data.get("liquidation", {})
        btc_correlation = data.get("btc_correlation", {})
        coin_news = data.get("coin_news", {})

        # 技术分析师: tech + multi_tf + volume_analysis + support_resistance
        tech_data = {
            "tech_indicators": tech,
            "multi_timeframe": multi_tf,
            "volume_analysis": volume_analysis,
            "support_resistance": support_resistance,
        }
        # 链上分析师: crypto + liquidation
        onchain_data = {
            "derivatives": crypto,
            "liquidation": liquidation,
        }
        # 情绪分析师: fear_greed + market_overview + global_news
        sentiment_data = {
            "fear_greed": fear_greed,
            "market_overview": market_overview,
            "global_news": global_news,
        }
        # 基本面分析师: coin_info + btc_correlation + coin_news
        fundamental_data = {
            "coin_info": coin_info,
            "btc_correlation": btc_correlation,
            "coin_news": coin_news,
        }

        tasks_for_symbol = [
            ("technical", {
                "prompt": f"分析 {symbol} 的技术指标数据:\n{json.dumps(tech_data, ensure_ascii=False, indent=2)}",
                "model": "haiku",
                "system_prompt": TECHNICAL_ANALYST,
                "json_schema": ANALYST_SCHEMA,
            }),
            ("onchain", {
                "prompt": f"分析 {symbol} 的链上与衍生品数据:\n{json.dumps(onchain_data, ensure_ascii=False, indent=2)}",
                "model": "haiku",
                "system_prompt": ONCHAIN_ANALYST,
                "json_schema": ANALYST_SCHEMA,
            }),
            ("sentiment", {
                "prompt": f"分析 {symbol} 的市场情绪:\n{json.dumps(sentiment_data, ensure_ascii=False, indent=2)}",
                "model": "haiku",
                "system_prompt": SENTIMENT_ANALYST,
                "json_schema": ANALYST_SCHEMA,
            }),
            ("fundamental", {
                "prompt": f"分析 {symbol} 的基本面数据:\n{json.dumps(fundamental_data, ensure_ascii=False, indent=2)}",
                "model": "haiku",
                "system_prompt": FUNDAMENTAL_ANALYST,
                "json_schema": ANALYST_SCHEMA,
            }),
        ]
        for analyst_type, task in tasks_for_symbol:
            task_index.append((symbol, analyst_type))
            all_tasks.append(task)

    # 并行调用，受 MAX_CONCURRENT 全局限制 (默认 2 并发)
    results = call_claude_parallel(all_tasks)

    # 按 symbol 重组结果
    analyses = {s: {} for s in screened}
    for i, result in enumerate(results):
        symbol, analyst_type = task_index[i]
        if isinstance(result, dict) and "error" in result:
            errors.append(f"analyze_{symbol}_{analyst_type}: {result['error']}")
        analyses[symbol][analyst_type] = result

    err_count = sum(1 for r in results if isinstance(r, dict) and "error" in r)
    _console.print(f"    完成: {len(results) - err_count}/{len(results)} 成功, 耗时 {time.time() - t0:.0f}s")
    return {"analyses": analyses, "errors": errors}


# ─── Node: research (所有币种 × 2 研究员 全并行) ────────────────────────

def research(state: WorkflowState) -> dict:
    """所有币种的看多/看空研究员并行（5币 × 2 = 10 个 sonnet，5 并发）"""
    analyses = state.get("analyses", {})
    n_tasks = len(analyses) * 2
    _stage(4, f"多空辩论 — {n_tasks} 个 sonnet ({len(analyses)} 币 x 看多/看空)")
    t0 = time.time()
    errors = list(state.get("errors", []))

    all_tasks = []
    task_index = []

    for symbol, analysis in analyses.items():
        analysis_text = json.dumps(analysis, ensure_ascii=False, indent=2)

        for role, prompt_prefix, sys_prompt, schema in [
            ("bull", "构建看涨论据", BULL_RESEARCHER, BULL_SCHEMA),
            ("bear", "构建看跌论据", BEAR_RESEARCHER, BEAR_SCHEMA),
        ]:
            task_index.append((symbol, role))
            all_tasks.append({
                "prompt": f"基于以下 {symbol} 的分析师报告，{prompt_prefix}:\n{analysis_text}",
                "model": "sonnet",
                "system_prompt": sys_prompt,
                "json_schema": schema,
            })

    results = call_claude_parallel(all_tasks)

    research_results = {s: {} for s in analyses}
    for i, result in enumerate(results):
        symbol, role = task_index[i]
        if isinstance(result, dict) and "error" in result:
            errors.append(f"research_{symbol}_{role}: {result['error']}")
        research_results[symbol][role] = result

    err_count = sum(1 for r in results if isinstance(r, dict) and "error" in r)
    _console.print(f"    完成: {len(results) - err_count}/{len(results)} 成功, 耗时 {time.time() - t0:.0f}s")
    return {"research": research_results, "errors": errors}


# ─── Node: trade (所有币种全并行) ──────────────────────────────────────────

def _build_portfolio_context() -> str:
    """构建当前持仓和账户上下文（供 TRADER/RISK_MANAGER 使用）"""
    from cryptobot.freqtrade_api import ft_api_get
    from cryptobot.config import load_settings

    positions = ft_api_get("/status") or []
    balance_data = ft_api_get("/balance")
    settings = load_settings()
    risk_cfg = settings.get("risk", {})

    # 解析余额
    usdt_balance = 0.0
    usdt_free = 0.0
    usdt_used = 0.0
    if balance_data:
        for cur in balance_data.get("currencies", []):
            if cur.get("currency") == "USDT":
                usdt_balance = float(cur.get("balance", 0))
                usdt_free = float(cur.get("free", 0))
                usdt_used = float(cur.get("used", 0))
                break

    if not positions and usdt_balance <= 0:
        return ""  # Freqtrade 未运行，不注入

    # 构建持仓摘要
    lines = [
        "### 账户状态",
        f"USDT 余额: {usdt_balance:.2f} (可用: {usdt_free:.2f}, 已用: {usdt_used:.2f})",
        f"当前持仓: {len(positions)} 个",
    ]
    long_used = 0.0
    short_used = 0.0
    for p in positions:
        direction = "SHORT" if p.get("is_short") else "LONG"
        pnl_pct = (p.get("profit_pct", 0) or 0) * 100
        lines.append(
            f"  - {p.get('pair', '?')} {direction} {p.get('leverage', '?')}x "
            f"盈亏:{pnl_pct:+.1f}%"
        )
        stake = float(p.get("stake_amount", 0) or 0)
        if p.get("is_short"):
            short_used += stake
        else:
            long_used += stake

    if usdt_balance > 0:
        lines.append(f"多头仓位占比: {long_used / usdt_balance * 100:.1f}%")
        lines.append(f"空头仓位占比: {short_used / usdt_balance * 100:.1f}%")
        lines.append(f"总仓位占比: {(long_used + short_used) / usdt_balance * 100:.1f}%")

    lines.append("")
    lines.append("### 风控规则")
    lines.append(f"- 同方向总仓位上限: {risk_cfg.get('max_same_direction_pct', 50)}%")
    lines.append(f"- 同类别最大: {risk_cfg.get('max_same_category_pct', 40)}%")
    lines.append(f"- 总持仓上限: {risk_cfg.get('max_total_position_pct', 80)}%")
    lines.append(f"- 单笔最大亏损: {risk_cfg.get('max_loss', {}).get('per_trade_pct', 2)}%")
    lines.append("")

    return "\n".join(lines)


def trade(state: WorkflowState) -> dict:
    """所有币种的交易决策并行（5 个 sonnet，5 并发）"""
    research_data = state.get("research", {})
    _stage(5, f"交易决策 — {len(research_data)} 个 sonnet")
    t0 = time.time()
    analyses = state.get("analyses", {})
    market_data = state.get("market_data", {})
    errors = list(state.get("errors", []))

    from cryptobot.config import get_pair_config

    # 获取持仓和账户上下文
    portfolio_ctx = _build_portfolio_context()

    # 获取历史绩效摘要
    perf_ctx = ""
    try:
        from cryptobot.journal.analytics import build_performance_summary
        perf_ctx = build_performance_summary(30)
    except Exception as e:
        logger.warning("绩效摘要生成失败: %s", e)

    # 市场状态上下文
    regime = state.get("market_regime", {})
    regime_ctx = ""
    if regime:
        regime_ctx = (
            f"### 当前市场状态\n"
            f"- 状态: {regime.get('regime', 'unknown')}\n"
            f"- {regime.get('description', '')}\n"
            f"- 建议最低置信度: {regime.get('params', {}).get('min_confidence', 60)}\n"
            f"- 建议最大杠杆: {regime.get('params', {}).get('max_leverage', 5)}x\n\n"
        )

    all_tasks = []
    task_meta = []  # (symbol, current_price)

    for symbol in research_data:
        bull = research_data[symbol].get("bull", {})
        bear = research_data[symbol].get("bear", {})
        analysis = analyses.get(symbol, {})
        data = market_data.get(symbol, {})
        pair_cfg = get_pair_config(symbol) or {}

        current_price = (data.get("tech") or {}).get("latest_close", 0)
        max_leverage = pair_cfg.get("leverage_range", [1, 3])[1]

        all_tasks.append({
            "prompt": (
                f"## {symbol} 交易决策\n\n"
                f"当前价格: {current_price}\n"
                f"最大杠杆: {max_leverage}x\n\n"
                f"{portfolio_ctx}"
                f"{perf_ctx}"
                f"{regime_ctx}"
                f"### 看多研究员观点\n{json.dumps(bull, ensure_ascii=False, indent=2)}\n\n"
                f"### 看空研究员观点\n{json.dumps(bear, ensure_ascii=False, indent=2)}\n\n"
                f"### 分析师数据\n{json.dumps(analysis, ensure_ascii=False, indent=2)}\n\n"
                f"请做出交易决策。"
            ),
            "model": "sonnet",
            "system_prompt": TRADER,
            "json_schema": TRADE_SCHEMA,
        })
        task_meta.append((symbol, current_price))

    results = call_claude_parallel(all_tasks)

    decisions = []
    for i, result in enumerate(results):
        symbol, current_price = task_meta[i]
        if isinstance(result, dict) and "error" not in result:
            result["symbol"] = symbol
            result["current_price"] = current_price
            decisions.append(result)
        else:
            err = result.get("error", "非 JSON 响应") if isinstance(result, dict) else "非 JSON 响应"
            errors.append(f"trade_{symbol}: {err}")

    actions = [f"{d['symbol']}={d.get('action', '?')}" for d in decisions]
    _console.print(f"    完成: {', '.join(actions) or '无交易'}, 耗时 {time.time() - t0:.0f}s")
    return {"decisions": decisions, "errors": errors}


# ─── Node: risk_review (LLM sonnet) ──────────────────────────────────────

def risk_review(state: WorkflowState) -> dict:
    """风控审核每个交易决策"""
    decisions = state.get("decisions", [])
    actionable = [d for d in decisions if d.get("action") != "no_trade"]
    _stage(6, f"风控审核 — {len(actionable)} 个决策")
    t0 = time.time()
    errors = list(state.get("errors", []))

    from cryptobot.config import load_settings, get_pair_config
    from cryptobot.risk.liquidation_calc import calc_liquidation_price, calc_liquidation_distance
    from cryptobot.signal.bridge import read_signals

    settings = load_settings()
    risk_cfg = settings.get("risk", {})
    existing_signals = read_signals()

    # 获取账户余额和持仓（用于仓位计算和硬性规则检查）
    from cryptobot.freqtrade_api import ft_api_get

    balance_data = ft_api_get("/balance")
    account_balance = 0.0
    if balance_data:
        for cur in balance_data.get("currencies", []):
            if cur.get("currency") == "USDT":
                account_balance = float(cur.get("balance", 0))
                break
    if account_balance <= 0:
        logger.warning("无法获取账户余额, 仓位计算将使用 AI 建议比例 fallback")

    positions = ft_api_get("/status") or []
    portfolio_ctx = _build_portfolio_context()

    # 获取历史绩效摘要
    perf_ctx = ""
    try:
        from cryptobot.journal.analytics import build_performance_summary
        perf_ctx = build_performance_summary(30)
    except Exception as e:
        logger.warning("绩效摘要生成失败: %s", e)

    # 市场状态上下文
    regime = state.get("market_regime", {})
    regime_ctx = ""
    if regime:
        regime_ctx = (
            f"### 当前市场状态\n"
            f"- 状态: {regime.get('regime', 'unknown')}\n"
            f"- {regime.get('description', '')}\n"
            f"- 建议最大杠杆: {regime.get('params', {}).get('max_leverage', 5)}x\n\n"
        )

    # 计算当前仓位占比（用于硬性规则）
    long_used = sum(float(p.get("stake_amount", 0) or 0) for p in positions if not p.get("is_short"))
    short_used = sum(float(p.get("stake_amount", 0) or 0) for p in positions if p.get("is_short"))
    total_used = long_used + short_used

    approved = []

    # 构建所有风控审核任务
    all_tasks = []
    task_decisions = []

    for decision in decisions:
        if decision.get("action") == "no_trade":
            continue

        symbol = decision.get("symbol", "")
        action = decision.get("action", "")
        leverage = decision.get("leverage", 3)
        entry_range = decision.get("entry_price_range", [])
        current_price = decision.get("current_price", 0)

        pair_cfg = get_pair_config(symbol) or {}

        # ── 硬性规则检查（不依赖 AI 判断）──
        if account_balance > 0:
            max_total_pct = risk_cfg.get("max_total_position_pct", 80)
            total_pct = total_used / account_balance * 100
            if total_pct >= max_total_pct:
                logger.info("硬性拒绝 %s: 总仓位 %.1f%% >= 上限 %d%%", symbol, total_pct, max_total_pct)
                _console.print(f"    [red]拒绝 {symbol}: 总仓位已达上限 {total_pct:.0f}%[/red]")
                continue

            max_dir_pct = risk_cfg.get("max_same_direction_pct", 50)
            dir_used = short_used if action == "short" else long_used
            dir_pct = dir_used / account_balance * 100
            if dir_pct >= max_dir_pct:
                dir_name = "空头" if action == "short" else "多头"
                logger.info("硬性拒绝 %s: %s仓位 %.1f%% >= 上限 %d%%", symbol, dir_name, dir_pct, max_dir_pct)
                _console.print(f"    [red]拒绝 {symbol}: {dir_name}仓位已达上限 {dir_pct:.0f}%[/red]")
                continue

        # 计算爆仓距离
        liq_info = ""
        if entry_range and len(entry_range) == 2 and entry_range[0]:
            entry_mid = (entry_range[0] + entry_range[1]) / 2
            liq_price = calc_liquidation_price(entry_mid, leverage, action)
            liq_dist = calc_liquidation_distance(current_price or entry_mid, liq_price)
            liq_info = f"爆仓价: {liq_price:.2f}, 爆仓距离: {liq_dist:.1f}%"

        existing = [s for s in existing_signals if s["symbol"] == symbol]

        all_tasks.append({
            "prompt": (
                f"## 风控审核: {symbol}\n\n"
                f"{portfolio_ctx}"
                f"{perf_ctx}"
                f"{regime_ctx}"
                f"### 交易决策\n{json.dumps(decision, ensure_ascii=False, indent=2)}\n\n"
                f"### 风控参数\n"
                f"- 最大杠杆: {pair_cfg.get('leverage_range', [1, 5])[1]}x\n"
                f"- 单笔最大亏损: {risk_cfg.get('max_loss', {}).get('per_trade_pct', 2)}%\n"
                f"- {liq_info}\n"
                f"- 现有持仓: {len(existing)} 个\n"
                f"- 现有信号: {json.dumps(existing, ensure_ascii=False, indent=2) if existing else '无'}\n\n"
                f"请进行风控审核。"
            ),
            "model": "sonnet",
            "system_prompt": RISK_MANAGER,
            "json_schema": RISK_SCHEMA,
        })
        task_decisions.append(decision)
        _console.print(f"    审核 {symbol} ({action})...")

    # 并行风控审核
    if all_tasks:
        results = call_claude_parallel(all_tasks)
        for i, result in enumerate(results):
            decision = task_decisions[i]
            symbol = decision.get("symbol", "")
            action = decision.get("action", "")
            if isinstance(result, dict) and "error" not in result:
                if result.get("decision") in ("approved", "modified"):
                    if result.get("decision") == "modified" and result.get("adjustments"):
                        for k, v in result["adjustments"].items():
                            if k in decision:
                                decision[k] = v
                    approved.append(_decision_to_signal(decision, result, account_balance))
                    logger.info("风控通过: %s %s", symbol, action)
                else:
                    reason = result.get("reasoning", "未知")
                    logger.info("风控拒绝: %s, 原因: %s", symbol, reason)
                    from cryptobot.notify import notify_risk_rejected
                    notify_risk_rejected(symbol, reason[:200])
            else:
                err = result.get("error", str(result)) if isinstance(result, dict) else str(result)
                errors.append(f"risk_{symbol}: {err}")

    _console.print(f"    完成: {len(approved)} 通过 / {len(task_decisions) - len(approved)} 拒绝, "
                    f"耗时 {time.time() - t0:.0f}s")
    return {"approved_signals": approved, "errors": errors}


def _decision_to_signal(
    decision: dict, risk_result: dict, account_balance: float
) -> dict:
    """将交易决策转换为信号格式，调用 position_sizer 计算仓位"""
    from cryptobot.risk.position_sizer import calc_position_size

    now = datetime.now(timezone.utc)
    leverage = decision.get("leverage", 3)
    entry_range = decision.get("entry_price_range")
    stop_loss = decision.get("stop_loss")

    # 计算精确仓位（需要入场价和止损价）
    position_size_usdt = None
    if entry_range and len(entry_range) == 2 and entry_range[0] and stop_loss and account_balance > 0:
        entry_price = (entry_range[0] + entry_range[1]) / 2
        try:
            sizing = calc_position_size(
                symbol=decision["symbol"],
                account_balance=account_balance,
                entry_price=entry_price,
                stop_loss_price=stop_loss,
                leverage=leverage,
            )
            position_size_usdt = sizing["margin_usdt"]
            logger.info(
                "仓位计算 %s: balance=%.0f, margin=%.0f, max_loss=%.0f (%.1f%%)",
                decision["symbol"], account_balance, sizing["margin_usdt"],
                sizing["max_loss_usdt"], sizing["max_loss_pct_of_balance"],
            )
        except (ValueError, KeyError) as e:
            logger.warning("仓位计算失败 %s: %s, 使用 AI 建议比例", decision["symbol"], e)

    # fallback: 用 AI 建议的百分比 × 余额
    if position_size_usdt is None:
        pct = decision.get("position_size_pct", 10)
        position_size_usdt = account_balance * pct / 100 if account_balance > 0 else 1000

    return {
        "symbol": decision["symbol"],
        "action": decision["action"],
        "leverage": leverage,
        "entry_price_range": entry_range,
        "stop_loss": stop_loss,
        "take_profit": decision.get("take_profit", []),
        "confidence": decision.get("confidence", 50),
        "position_size_usdt": round(position_size_usdt, 2),
        "analysis_summary": {
            "reasoning": decision.get("reasoning", ""),
            "risk_score": risk_result.get("risk_score"),
            "warnings": risk_result.get("warnings", []),
        },
        "timestamp": now.isoformat(),
    }


# ─── Node: execute ───────────────────────────────────────────────────────

def execute(state: WorkflowState) -> dict:
    """根据 realtime.enabled 配置写入 signal.json 或 pending_signals.json"""
    from cryptobot.config import load_settings
    from cryptobot.signal.bridge import write_signal, write_pending_signal

    settings = load_settings()
    realtime_enabled = settings.get("realtime", {}).get("enabled", False)

    approved = state.get("approved_signals", [])
    target = "pending_signals.json" if realtime_enabled else "signal.json"
    _stage(7, f"写入信号 — {len(approved)} 个 → {target}")
    errors = list(state.get("errors", []))
    executed = []

    writer = write_pending_signal if realtime_enabled else write_signal

    from cryptobot.notify import notify_new_signal, notify_workflow_error
    from cryptobot.journal.models import SignalRecord
    from cryptobot.journal.storage import save_record

    for signal in approved:
        try:
            result = writer(signal)
            executed.append(result)
            logger.info("信号写入成功: %s %s → %s", signal["symbol"], signal["action"], target)
            notify_new_signal(result)
            # 记录到交易日志
            try:
                save_record(SignalRecord.from_signal(result))
            except Exception as je:
                logger.warning("交易日志记录失败: %s", je)
        except Exception as e:
            logger.error("信号写入失败 %s: %s", signal["symbol"], e)
            errors.append(f"execute_{signal['symbol']}: {e}")

    if len(errors) >= 3:
        notify_workflow_error(len(errors), errors)

    return {"executed": executed, "errors": errors}


# ─── 条件路由 ─────────────────────────────────────────────────────────────

def should_risk_review(state: WorkflowState) -> str:
    """trade → risk_review 或 END"""
    decisions = state.get("decisions", [])
    actionable = [d for d in decisions if d.get("action") != "no_trade"]
    if actionable:
        return "risk_review"
    logger.info("无可执行交易决策，工作流结束")
    return END


def should_execute(state: WorkflowState) -> str:
    """risk_review → execute 或 END"""
    approved = state.get("approved_signals", [])
    if approved:
        return "execute"
    logger.info("无通过风控的信号，工作流结束")
    return END


# ─── 图构建 ───────────────────────────────────────────────────────────────

def build_graph() -> StateGraph:
    """构建并编译 LangGraph 工作流"""
    graph = StateGraph(WorkflowState)

    # 添加节点
    graph.add_node("collect_data", collect_data)
    graph.add_node("screen", screen)
    graph.add_node("analyze", analyze)
    graph.add_node("research", research)
    graph.add_node("trade", trade)
    graph.add_node("risk_review", risk_review)
    graph.add_node("execute", execute)

    # 线性边
    graph.add_edge("collect_data", "screen")
    graph.add_edge("screen", "analyze")
    graph.add_edge("analyze", "research")
    graph.add_edge("research", "trade")

    # 条件路由
    graph.add_conditional_edges("trade", should_risk_review)
    graph.add_conditional_edges("risk_review", should_execute)

    # 入口
    graph.set_entry_point("collect_data")

    return graph.compile()


# ─── 独立流程: 持仓复审 ──────────────────────────────────────────────────

def re_review(positions: list[dict], state: dict) -> list[dict]:
    """对现有持仓进行 AI 重新评估

    Args:
        positions: Freqtrade /status 返回的持仓列表
        state: collect_data_for_symbols 返回的结构化数据
               {market_data, fear_greed, market_overview, global_news}

    Returns:
        评估建议列表 [{symbol, decision, new_stop_loss, reasoning}, ...]
    """
    from cryptobot.workflow.prompts import RE_REVIEWER, RE_REVIEW_SCHEMA, ANALYST_SCHEMA
    from cryptobot.workflow.prompts import (
        TECHNICAL_ANALYST, ONCHAIN_ANALYST, SENTIMENT_ANALYST, FUNDAMENTAL_ANALYST,
    )

    if not positions:
        return []

    market_data = state.get("market_data", {})
    fear_greed = state.get("fear_greed", {})
    market_overview = state.get("market_overview", {})
    global_news = state.get("global_news", {})

    _console.print(f"[cyan]持仓复审 — {len(positions)} 个持仓[/cyan]")

    # Step 1: 为每个持仓币种运行 4 位分析师
    analyses = {}
    all_tasks = []
    task_index = []

    for pos in positions:
        pair = pos.get("pair", "")
        symbol = pair.replace("/", "").replace(":USDT", "")
        data = market_data.get(symbol, {})

        if not data.get("tech"):
            continue

        for analyst_type, sys_prompt, data_key in [
            ("technical", TECHNICAL_ANALYST, {
                "tech_indicators": data.get("tech"),
                "multi_timeframe": data.get("multi_tf"),
                "volume_analysis": data.get("volume_analysis"),
                "support_resistance": data.get("support_resistance"),
            }),
            ("onchain", ONCHAIN_ANALYST, {
                "derivatives": data.get("crypto"),
                "liquidation": data.get("liquidation"),
            }),
            ("sentiment", SENTIMENT_ANALYST, {
                "fear_greed": fear_greed,
                "market_overview": market_overview,
                "global_news": global_news,
            }),
            ("fundamental", FUNDAMENTAL_ANALYST, {
                "coin_info": data.get("coin_info"),
                "btc_correlation": data.get("btc_correlation"),
                "coin_news": data.get("coin_news"),
            }),
        ]:
            task_index.append((symbol, analyst_type))
            all_tasks.append({
                "prompt": f"分析 {symbol} 的最新数据:\n{json.dumps(data_key, ensure_ascii=False, indent=2)}",
                "model": "haiku",
                "system_prompt": sys_prompt,
                "json_schema": ANALYST_SCHEMA,
            })

    if all_tasks:
        analyst_results = call_claude_parallel(all_tasks)
        for i, result in enumerate(analyst_results):
            symbol, analyst_type = task_index[i]
            if symbol not in analyses:
                analyses[symbol] = {}
            analyses[symbol][analyst_type] = result

    # Step 2: 对每个持仓运行复审
    review_tasks = []
    review_positions = []

    for pos in positions:
        pair = pos.get("pair", "")
        symbol = pair.replace("/", "").replace(":USDT", "")
        analysis = analyses.get(symbol, {})

        if not analysis:
            continue

        review_tasks.append({
            "prompt": (
                f"## 持仓复审: {symbol}\n\n"
                f"### 当前持仓\n"
                f"- 方向: {'空' if pos.get('is_short') else '多'}\n"
                f"- 入场价: {pos.get('open_rate')}\n"
                f"- 当前价: {pos.get('current_rate')}\n"
                f"- 盈亏: {pos.get('profit_pct', 0):.2%}\n"
                f"- 杠杆: {pos.get('leverage')}x\n"
                f"- 当前止损: {pos.get('stop_loss_abs')}\n"
                f"- 持仓时长: {pos.get('trade_duration')}\n\n"
                f"### 最新分析师报告\n"
                f"{json.dumps(analysis, ensure_ascii=False, indent=2)}\n\n"
                f"请评估此持仓是否需要调整。"
            ),
            "model": "sonnet",
            "system_prompt": RE_REVIEWER,
            "json_schema": RE_REVIEW_SCHEMA,
        })
        review_positions.append(pos)

    if not review_tasks:
        return []

    _console.print(f"    运行 {len(review_tasks)} 个复审...")
    review_results = call_claude_parallel(review_tasks)

    suggestions = []
    for i, result in enumerate(review_results):
        pos = review_positions[i]
        pair = pos.get("pair", "")
        symbol = pair.replace("/", "").replace(":USDT", "")

        if isinstance(result, dict) and "error" not in result:
            suggestions.append({
                "symbol": symbol,
                "pair": pair,
                "decision": result.get("decision", "hold"),
                "new_stop_loss": result.get("new_stop_loss"),
                "reasoning": result.get("reasoning", ""),
                "risk_level": result.get("risk_level", "medium"),
            })
        else:
            logger.error("复审失败 %s: %s", symbol, result)

    return suggestions
