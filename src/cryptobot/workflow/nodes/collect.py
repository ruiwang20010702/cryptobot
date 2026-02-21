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

    # 恐惧贪婪极端值可升级为 volatile
    fg_val = fear_greed.get("current_value", 50)
    is_volatile_upgrade = False
    if (fg_val < 20 or fg_val > 80) and regime_result["regime"] != "volatile":
        regime_result["regime"] = "volatile"
        regime_result["description"] += " (恐惧贪婪极端值触发升级)"
        is_volatile_upgrade = True

    # 平滑 regime 切换 (防止边界反复跳动)
    from cryptobot.regime_smoother import smooth_regime_transition
    from cryptobot.config import load_settings

    settings = load_settings()
    confirm_cycles = settings.get("market_regime", {}).get("smoothing_cycles", 2)
    is_simulation = bool(market_data.get("_klines_override"))
    old_regime = regime_result["regime"]

    smoothed_regime, regime_changed = smooth_regime_transition(
        regime_result["regime"],
        confirm_cycles=confirm_cycles,
        is_volatile_upgrade=is_volatile_upgrade,
        is_simulation=is_simulation,
    )
    regime_result["regime"] = smoothed_regime

    if regime_changed:
        from cryptobot.notify import notify_regime_change
        notify_regime_change(old_regime, smoothed_regime, _calc_confidence(regime_result))

    # 附加策略参数 (复用现有 _REGIME_PARAMS)
    regime_key = regime_result["regime"]
    params = _REGIME_PARAMS.get(regime_key, _REGIME_PARAMS["ranging"])

    return {
        "regime": regime_key,
        "confidence": _calc_confidence(regime_result),
        "params": {k: v for k, v in params.items() if k != "description"},
        "description": regime_result["description"],
        "trend_direction": regime_result.get("trend_direction", "neutral"),
        "trend_strength": regime_result.get("trend_strength", "weak"),
        "volatility_state": regime_result.get("volatility_state", "normal"),
        "timeframe_details": regime_result.get("timeframe_details", {}),
    }


def collect_data(state: WorkflowState) -> dict:
    """采集所有币种的市场数据（纯 Python，不调 LLM）"""
    from cryptobot.config import get_all_symbols

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

    # 宏观风险标注
    if macro_events.get("has_high_impact"):
        regime["macro_risk"] = True
        next_ev = macro_events.get("next_high_impact")
        if next_ev:
            regime["description"] += (
                f" (宏观风险: {next_ev['event']} in {next_ev['hours_until']}h)"
            )

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
        "errors": errors,
    }
