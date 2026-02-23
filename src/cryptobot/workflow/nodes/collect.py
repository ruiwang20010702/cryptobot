"""Node: collect_data — 采集所有币种的市场数据"""

import logging
import time

from rich.console import Console

from cryptobot.workflow.state import WorkflowState
from cryptobot.workflow.utils import _stage, fetch_market_data

logger = logging.getLogger(__name__)
_console = Console()


# ─── 市场状态检测 ─────────────────────────────────────────────────────────

# 各状态的策略参数默认值
_REGIME_PARAMS = {
    "trending": {
        "min_confidence": 58,
        "max_leverage": 5,
        "trailing_stop": True,
        "description": "趋势市: EMA 多头/空头排列，ADX>25。适合顺势交易，可适度加仓。",
    },
    "ranging": {
        "min_confidence": 63,
        "max_leverage": 3,
        "trailing_stop": False,
        "description": "震荡市: ADX<20，布林带收窄。区间交易为主，轻仓博反弹。",
    },
    "volatile": {
        "min_confidence": 68,
        "max_leverage": 2,
        "trailing_stop": True,
        "description": "剧烈波动: ATR 显著放大，恐惧贪婪极端。降低杠杆，严格止损。",
    },
    "volatile_normal": {
        "min_confidence": 75,
        "max_leverage": 1,
        "max_positions": 1,
        "trailing_stop": True,
        "description": "高波动(中性): 保守趋势跟踪，1x 杠杆，仅最高置信度信号。",
    },
    "volatile_fear": {
        "min_confidence": 90,
        "max_leverage": 1,
        "max_positions": 1,
        "trailing_stop": False,
        "description": "高波动(恐惧): 费率套利+宽网格，禁止方向性交易。",
    },
    "volatile_greed": {
        "min_confidence": 80,
        "max_leverage": 1,
        "max_positions": 1,
        "direction_bias": "short",
        "trailing_stop": True,
        "description": "高波动(贪婪): 仅做空，80+ 置信度，过热回调策略。",
    },
}


def _calc_confidence(regime_result: dict) -> int:
    """根据多 TF 一致性计算置信度"""
    details = regime_result.get("timeframe_details", {})
    if not details:
        return 50

    trends = [d["trend"] for d in details.values()]
    majority = max(set(trends), key=trends.count)
    agreement = trends.count(majority) / len(trends)

    # 强 ADX 加分
    strong_count = sum(1 for d in details.values() if d.get("strength") == "strong")

    base = int(agreement * 80)
    bonus = strong_count * 5
    return min(95, base + bonus)


def _detect_market_regime(market_data: dict, fear_greed: dict) -> dict:
    """基于多时间框架 + 恐惧贪婪判断市场状态

    Returns:
        {regime, confidence, params, description, trend_direction,
         trend_strength, volatility_state, timeframe_details}
    """
    from cryptobot.indicators.regime import detect_regime

    try:
        regime_result = detect_regime("BTCUSDT")
    except Exception as e:
        logger.warning("多TF regime 检测失败，使用默认: %s", e)
        regime_result = {
            "regime": "ranging",
            "trend_direction": "neutral",
            "trend_strength": "weak",
            "volatility_state": "normal",
            "timeframe_details": {},
            "description": "检测失败，默认震荡市",
        }

    # 恐惧贪婪极端值可升级为 volatile (R6-L5: 不可变模式)
    fg_val = fear_greed.get("current_value", 50)
    is_volatile_upgrade = False
    old_regime = regime_result["regime"]  # 升级前保存
    if (fg_val < 20 or fg_val > 80) and regime_result["regime"] != "volatile":
        regime_result = {
            **regime_result,
            "regime": "volatile",
            "description": regime_result["description"] + " (恐惧贪婪极端值触发升级)",
        }
        is_volatile_upgrade = True

    # 平滑 regime 切换 (防止边界反复跳动)
    from cryptobot.regime_smoother import smooth_regime_transition
    from cryptobot.config import load_settings

    settings = load_settings()
    confirm_cycles = settings.get("market_regime", {}).get("smoothing_cycles", 2)
    is_simulation = bool(market_data.get("_klines_override"))

    smoothed_regime, regime_changed = smooth_regime_transition(
        regime_result["regime"],
        confirm_cycles=confirm_cycles,
        is_volatile_upgrade=is_volatile_upgrade,
        is_simulation=is_simulation,
    )
    regime_result = {**regime_result, "regime": smoothed_regime}

    if regime_changed:
        from cryptobot.notify import notify_regime_change
        notify_regime_change(old_regime, smoothed_regime, _calc_confidence(regime_result))

    # 附加策略参数 (用户配置优先合并)
    regime_key = regime_result["regime"]
    default_params = _REGIME_PARAMS.get(regime_key, _REGIME_PARAMS["ranging"])
    user_regime_cfg = settings.get("market_regime", {}).get(regime_key, {})
    params = {**default_params, **user_regime_cfg}

    return {
        "regime": regime_key,
        "confidence": _calc_confidence(regime_result),
        "params": {k: v for k, v in params.items() if k != "description"},
        "description": regime_result["description"],
        "trend_direction": regime_result.get("trend_direction", "neutral"),
        "trend_strength": regime_result.get("trend_strength", "weak"),
        "volatility_state": regime_result.get("volatility_state", "normal"),
        "timeframe_details": regime_result.get("timeframe_details", {}),
        "fear_greed_value": fg_val,
        "hurst_exponent": regime_result.get("hurst_exponent", 0.5),
        "regime_confidence": regime_result.get("regime_confidence", 0.0),
    }


def collect_data(state: WorkflowState) -> dict:
    """采集所有币种的市场数据（纯 Python，不调 LLM）"""
    from cryptobot.config import get_all_symbols
    from cryptobot.workflow.prompts import reset_prompt_version_cache
    from cryptobot.workflow.llm import reset_provider_cache

    # M11+M12: 每轮重置缓存，确保读取最新配置
    reset_prompt_version_cache()
    reset_provider_cache()

    errors = list(state.get("errors", []))
    symbols = get_all_symbols()
    _stage(1, f"数据采集 — {len(symbols)} 个币种 (扩展数据)")
    t0 = time.time()

    market_data, fear_greed, market_overview, global_news, stablecoin_flows, macro_events, fetch_errors = (
        fetch_market_data(symbols)
    )
    errors.extend(fetch_errors)

    ok = sum(1 for d in market_data.values() if d.get("tech"))
    fail_rate = (len(symbols) - ok) / len(symbols) if symbols else 1

    # R6-C4: 数据源可用率统计
    _source_checks = [
        ("fear_greed", fear_greed), ("market_overview", market_overview),
        ("global_news", global_news), ("stablecoin_flows", stablecoin_flows),
        ("macro_events", macro_events),
    ]
    available_sources = sum(1 for _, val in _source_checks if val)
    total_sources = len(_source_checks)
    data_availability = available_sources / total_sources if total_sources else 0
    data_quality = "normal" if data_availability >= 0.5 else "degraded"
    if data_quality == "degraded":
        logger.warning("数据源可用率 %.0f%% < 50%%, 降低置信度上限", data_availability * 100)

    _console.print(f"    完成: {ok}/{len(symbols)} 有技术数据, "
                    f"恐惧贪婪={fear_greed.get('current_value', '?')}, "
                    f"数据源={available_sources}/{total_sources}, "
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
    # R6-C4: 数据质量降级时注入标记并限制置信度
    if data_quality == "degraded":
        capped_conf = min(regime.get("confidence", 50), 60)
        regime = {**regime, "data_quality": "degraded", "confidence": capped_conf}
    else:
        regime = {**regime, "data_quality": "normal"}
    _console.print(f"    市场状态: {regime['regime']} (置信度 {regime['confidence']}%)")

    # 记录 volatile 周期（仅累计，不做决策）
    # R5-C5: volatile 策略已启用时不标记为观望
    try:
        from cryptobot.evolution.volatile_toggle import (
            record_volatile_cycle,
            is_volatile_strategy_enabled,
        )
        is_observe = regime["regime"] == "volatile" and not is_volatile_strategy_enabled()
        record_volatile_cycle(regime["regime"], is_observe)
    except Exception as e:
        logger.debug("volatile_toggle 记录跳过: %s", e)

    # DXY 美元指数 (单独获取，不扩展 fetch_market_data tuple)
    dxy_data = {}
    try:
        from cryptobot.data.dxy import get_dxy_trend
        dxy_data = get_dxy_trend()
    except Exception as e:
        logger.warning("DXY 数据获取失败: %s", e)
        errors.append(f"dxy: {e}")

    # 资金层级检测
    capital_tier = {}
    try:
        from cryptobot.capital_strategy import get_balance_from_freqtrade, detect_capital_tier
        balance = get_balance_from_freqtrade()
        capital_tier = detect_capital_tier(balance)
        _console.print(f"    资金层级: {capital_tier['tier']} (余额 ${balance:.0f})")
    except Exception as e:
        logger.warning("资金层级检测失败: %s", e)
        errors.append(f"capital_tier: {e}")

    # 宏观风险标注 (不可变)
    if macro_events.get("has_high_impact"):
        next_ev = macro_events.get("next_high_impact")
        macro_desc = ""
        if next_ev:
            macro_desc = f" (宏观风险: {next_ev['event']} in {next_ev['hours_until']}h)"
        regime = {**regime, "macro_risk": True, "description": regime.get("description", "") + macro_desc}

    # P5: 学习反馈环 — 注入历史绩效摘要
    perf_feedback = {}
    try:
        from cryptobot.journal.analytics import calc_performance
        perf = calc_performance(30)
        if perf.get("closed", 0) >= 30:
            perf_feedback = {
                "win_rate": perf["win_rate"],
                "profit_factor": perf["profit_factor"],
                "avg_pnl_pct": perf["avg_pnl_pct"],
                "by_symbol": perf.get("by_symbol", {}),
            }
    except Exception as e:
        logger.warning("历史绩效摘要加载失败: %s", e)

    # 特征工程（非关键路径）
    try:
        from datetime import datetime, timezone
        from cryptobot.features.pipeline import build_feature_vector, FeatureMatrix
        from cryptobot.features.feature_store import save_features

        ts = datetime.now(tz=timezone.utc).isoformat()
        vectors = []
        for symbol in symbols:
            sym_data = market_data.get(symbol, {})
            vec = build_feature_vector(
                symbol=symbol,
                timestamp=ts,
                tech=sym_data.get("tech"),
                multi_tf=sym_data.get("multi_tf"),
                crypto=sym_data.get("crypto"),
                fear_greed=fear_greed,
                news=global_news,
                orderbook=sym_data.get("orderbook"),
                dxy=dxy_data,
                macro=macro_events,
                stablecoin=stablecoin_flows,
                btc_corr=sym_data.get("btc_correlation") or 0.0,
            )
            vectors.append(vec)

        if vectors:
            feat_names = sorted(vectors[0].features.keys())
            matrix = FeatureMatrix(vectors=vectors, feature_names=feat_names)
            save_features(matrix)
    except Exception as e:
        logger.debug("特征提取跳过: %s", e)

    return {
        "market_data": market_data,
        "market_overview": market_overview,
        "fear_greed": fear_greed,
        "global_news": global_news,
        "stablecoin_flows": stablecoin_flows,
        "macro_events": macro_events,
        "dxy_data": dxy_data,
        "market_regime": regime,
        "capital_tier": capital_tier,
        "perf_feedback": perf_feedback,
        "errors": errors,
    }
