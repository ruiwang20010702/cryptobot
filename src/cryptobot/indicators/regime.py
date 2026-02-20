"""多时间框架市场状态检测 (regime detection)

替换 collect 节点中的单 TF BTC 4h 检测，升级为 1h/4h/1d 三时间框架投票。
"""

import logging

import numpy as np
import talib

from cryptobot.indicators.calculator import load_klines

logger = logging.getLogger(__name__)

_TIMEFRAMES = ("1h", "4h", "1d")

_DEFAULT_RESULT = {
    "regime": "ranging",
    "trend_direction": "neutral",
    "trend_strength": "weak",
    "volatility_state": "normal",
    "timeframe_details": {},
    "description": "数据不足，默认震荡市",
}


def _classify_volatility(atr_pcts: list[float]) -> str:
    """根据 ATR% 判定波动率状态，以 4h 为主"""
    if not atr_pcts:
        return "normal"

    # 优先取 4h (index 1)，否则取均值
    val = atr_pcts[1] if len(atr_pcts) > 1 else atr_pcts[0]

    if val > 3.0:
        return "high_vol"
    if val < 1.0:
        return "low_vol"
    return "normal"


def _analyze_timeframe(symbol: str, timeframe: str) -> dict:
    """分析单个时间框架的趋势和强度

    Returns:
        {"trend": "bullish"|"bearish", "strength": "strong"|"weak",
         "adx": float, "atr_pct": float}
    """
    df = load_klines(symbol, timeframe)

    close = df["close"].values.astype(np.float64)
    high = df["high"].values.astype(np.float64)
    low = df["low"].values.astype(np.float64)

    ema20 = talib.EMA(close, timeperiod=20)
    ema50 = talib.EMA(close, timeperiod=50)
    adx = talib.ADX(high, low, close, timeperiod=14)
    atr = talib.ATR(high, low, close, timeperiod=14)

    trend = "bullish" if ema20[-1] > ema50[-1] else "bearish"
    strength = "strong" if adx[-1] > 25 else "weak"
    atr_pct = float(atr[-1] / close[-1] * 100)

    return {
        "trend": trend,
        "strength": strength,
        "adx": round(float(adx[-1]), 1),
        "atr_pct": round(atr_pct, 2),
    }


def _build_description(regime: str, trend_direction: str, agreement: str) -> str:
    """根据 regime + trend_direction 生成中文描述"""
    regime_labels = {
        "trending": "趋势市",
        "ranging": "震荡市",
        "volatile": "高波动",
    }
    direction_labels = {
        "bullish": "多头",
        "bearish": "空头",
        "neutral": "方向不明",
    }
    label = regime_labels.get(regime, regime)
    dir_label = direction_labels.get(trend_direction, trend_direction)
    return f"{label} ({dir_label}): {agreement}"


def detect_regime(symbol: str = "BTCUSDT") -> dict:
    """多时间框架投票检测市场状态

    Returns:
        {regime, trend_direction, trend_strength, volatility_state,
         timeframe_details, description}
    """
    details: dict[str, dict] = {}
    atr_pcts: list[float] = []

    for tf in _TIMEFRAMES:
        try:
            result = _analyze_timeframe(symbol, tf)
            details[tf] = result
            atr_pcts.append(result["atr_pct"])
        except Exception:
            logger.warning("TF %s 加载失败，跳过", tf, exc_info=True)

    if not details:
        return {**_DEFAULT_RESULT}

    # --- 多 TF 投票 ---
    trends = [d["trend"] for d in details.values()]
    bullish_count = trends.count("bullish")
    bearish_count = trends.count("bearish")
    total = len(trends)

    if bullish_count * 2 >= total + 1:
        trend_direction = "bullish"
    elif bearish_count * 2 >= total + 1:
        trend_direction = "bearish"
    else:
        trend_direction = "neutral"

    # --- 趋势强度 ---
    strong_count = sum(1 for d in details.values() if d["strength"] == "strong")
    trend_strength = "strong" if strong_count >= 1 else "weak"

    # --- 波动率 ---
    volatility_state = _classify_volatility(atr_pcts)

    # --- Regime 判定 ---
    has_strong_adx = any(d["adx"] > 25 for d in details.values())

    if has_strong_adx and trend_direction != "neutral":
        regime = "trending"
    elif volatility_state == "high_vol":
        regime = "volatile"
    else:
        regime = "ranging"

    # --- 描述 ---
    agreement = (
        f"{max(bullish_count, bearish_count)}/{total} 时间框架确认"
        f"{'上涨' if trend_direction == 'bullish' else '下跌' if trend_direction == 'bearish' else '无明确'}趋势"
    )
    description = _build_description(regime, trend_direction, agreement)

    return {
        "regime": regime,
        "trend_direction": trend_direction,
        "trend_strength": trend_strength,
        "volatility_state": volatility_state,
        "timeframe_details": details,
        "description": description,
    }
