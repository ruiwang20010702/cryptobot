"""多时间框架分析 + 支撑阻力 + 量价分析

各时间框架统一通过 load_klines 加载（本地 feather 优先，回退 Binance API）。
"""

import math

import numpy as np
import talib

from cryptobot.indicators.calculator import load_klines, _safe


# ─── 单时间框架指标摘要 ──────────────────────────────────────────────────

def _tf_summary(close: np.ndarray, high: np.ndarray, low: np.ndarray) -> dict:
    """计算单个时间框架的核心指标摘要"""
    ema_7 = talib.EMA(close, timeperiod=7)
    ema_25 = talib.EMA(close, timeperiod=25)
    ema_99 = talib.EMA(close, timeperiod=99)
    rsi = talib.RSI(close, timeperiod=14)
    macd, macd_sig, macd_hist = talib.MACD(close, 12, 26, 9)
    adx = talib.ADX(high, low, close, timeperiod=14)

    e7, e25, e99 = _safe(ema_7[-1]), _safe(ema_25[-1]), _safe(ema_99[-1])

    # EMA 排列
    if None not in (e7, e25, e99):
        if e7 > e25 > e99:
            alignment = "bullish"
        elif e7 < e25 < e99:
            alignment = "bearish"
        else:
            alignment = "mixed"
    else:
        alignment = "unknown"

    # MACD 交叉
    h_curr = _safe(macd_hist[-1]) if len(macd_hist) >= 1 else None
    h_prev = _safe(macd_hist[-2]) if len(macd_hist) > 1 else None
    if h_curr is not None and h_prev is not None:
        if h_prev <= 0 < h_curr:
            macd_cross = "golden_cross"
        elif h_prev >= 0 > h_curr:
            macd_cross = "death_cross"
        else:
            macd_cross = "none"
    else:
        macd_cross = "none"

    rsi_val = _safe(rsi[-1])
    adx_val = _safe(adx[-1])

    # 趋势方向
    if alignment == "bullish" and (rsi_val or 50) > 45:
        direction = "bullish"
    elif alignment == "bearish" and (rsi_val or 50) < 55:
        direction = "bearish"
    else:
        direction = "neutral"

    # 趋势强度 0-100
    strength = 0
    if alignment in ("bullish", "bearish"):
        strength += 30
    if adx_val and adx_val > 25:
        strength += min(30, adx_val - 25)
    if rsi_val:
        strength += abs(rsi_val - 50) * 0.4
    if macd_cross in ("golden_cross", "death_cross"):
        strength += 15
    strength = min(100, strength)

    return {
        "ema_alignment": alignment,
        "rsi": rsi_val,
        "macd_cross": macd_cross,
        "adx": adx_val,
        "direction": direction,
        "trend_strength": round(strength, 1),
    }


# ─── 多时间框架共振 ──────────────────────────────────────────────────────

def calc_multi_timeframe(symbol: str) -> dict:
    """计算 1h/4h/1d 三个时间框架的指标并判断共振"""
    results = {}

    # 1h
    try:
        df_1h = load_klines(symbol, "1h")
        c, hi, lo = df_1h["close"].values.astype(np.float64), df_1h["high"].values.astype(np.float64), df_1h["low"].values.astype(np.float64)
        results["1h"] = _tf_summary(c, hi, lo)
    except FileNotFoundError:
        results["1h"] = {"direction": "unknown", "trend_strength": 0}

    # 4h
    try:
        df_4h = load_klines(symbol, "4h")
        c, hi, lo = df_4h["close"].values.astype(np.float64), df_4h["high"].values.astype(np.float64), df_4h["low"].values.astype(np.float64)
        results["4h"] = _tf_summary(c, hi, lo)
    except FileNotFoundError:
        results["4h"] = {"direction": "unknown", "trend_strength": 0}

    # 1d (load_klines 自动回退 Binance API)
    try:
        df_1d = load_klines(symbol, "1d")
        c = df_1d["close"].values.astype(np.float64)
        hi = df_1d["high"].values.astype(np.float64)
        lo = df_1d["low"].values.astype(np.float64)
        results["1d"] = _tf_summary(c, hi, lo)
    except Exception:
        results["1d"] = {"direction": "unknown", "trend_strength": 0}

    # 共振分析
    directions = [results[tf]["direction"] for tf in ("1h", "4h", "1d")]
    bullish_count = sum(1 for d in directions if d == "bullish")
    bearish_count = sum(1 for d in directions if d == "bearish")

    if bullish_count >= 2:
        aligned_direction = "bullish"
        aligned_count = bullish_count
    elif bearish_count >= 2:
        aligned_direction = "bearish"
        aligned_count = bearish_count
    else:
        aligned_direction = "mixed"
        aligned_count = 0

    # 共振置信度加成: 3/3 一致 +15, 2/3 一致 +8
    if aligned_count == 3:
        confidence_boost = 15
    elif aligned_count == 2:
        confidence_boost = 8
    else:
        confidence_boost = 0

    return {
        "symbol": symbol,
        "timeframes": results,
        "aligned_direction": aligned_direction,
        "aligned_count": aligned_count,
        "confidence_boost": confidence_boost,
    }


# ─── 支撑阻力位 ────────────────────────────────────────────────────────

def calc_support_resistance(symbol: str) -> dict:
    """计算支撑阻力位 (Pivot Points + Fibonacci + Swing)"""
    try:
        df_4h = load_klines(symbol, "4h")
    except FileNotFoundError:
        return {"error": f"无 4h 数据: {symbol}"}

    close = df_4h["close"].values.astype(np.float64)
    high = df_4h["high"].values.astype(np.float64)
    low = df_4h["low"].values.astype(np.float64)
    latest_close = close[-1]

    # 1) 日线 Pivot Points (用最近 6 根 4h = 1 天的 H/L/C)
    day_h = float(np.max(high[-6:]))
    day_l = float(np.min(low[-6:]))
    day_c = float(close[-1])
    pivot = (day_h + day_l + day_c) / 3
    r1 = 2 * pivot - day_l
    s1 = 2 * pivot - day_h
    r2 = pivot + (day_h - day_l)
    s2 = pivot - (day_h - day_l)

    # 2) Fibonacci 回撤 (基于最近 50 根 4h 的高低)
    lookback = min(50, len(high))
    swing_high = float(np.max(high[-lookback:]))
    swing_low = float(np.min(low[-lookback:]))
    fib_range = swing_high - swing_low

    fib_382 = swing_high - fib_range * 0.382
    fib_500 = swing_high - fib_range * 0.5
    fib_618 = swing_high - fib_range * 0.618

    # 3) 整数关口 (找最近的整数关口)
    magnitude = 10 ** max(0, int(math.log10(latest_close)) - 1)
    round_level = round(latest_close / magnitude) * magnitude
    round_levels = [round_level - magnitude, round_level, round_level + magnitude]

    # 4) 综合支撑阻力
    supports = sorted([s1, s2, fib_618, fib_500, swing_low] + [r for r in round_levels if r < latest_close], reverse=True)
    resistances = sorted([r1, r2, fib_382, swing_high] + [r for r in round_levels if r > latest_close])

    # 最近的支撑和阻力
    nearest_support = max([s for s in supports if s < latest_close], default=s2)
    nearest_resistance = min([r for r in resistances if r > latest_close], default=r1)

    # 支撑阻力比 (越大越接近阻力，越小越接近支撑)
    sr_range = nearest_resistance - nearest_support
    sr_ratio = (latest_close - nearest_support) / sr_range if sr_range > 0 else 0.5

    return {
        "symbol": symbol,
        "latest_close": round(latest_close, 2),
        "pivot_points": {
            "pivot": round(pivot, 2),
            "r1": round(r1, 2),
            "r2": round(r2, 2),
            "s1": round(s1, 2),
            "s2": round(s2, 2),
        },
        "fibonacci": {
            "swing_high": round(swing_high, 2),
            "swing_low": round(swing_low, 2),
            "fib_0.382": round(fib_382, 2),
            "fib_0.500": round(fib_500, 2),
            "fib_0.618": round(fib_618, 2),
        },
        "round_levels": [round(r, 2) for r in round_levels],
        "nearest_support": round(nearest_support, 2),
        "nearest_resistance": round(nearest_resistance, 2),
        "sr_ratio": round(sr_ratio, 4),
    }


# ─── 量价分析 ───────────────────────────────────────────────────────────

def calc_volume_analysis(symbol: str) -> dict:
    """VWAP + 量比 + OBV 量价背离"""
    try:
        df = load_klines(symbol, "4h")
    except FileNotFoundError:
        return {"error": f"无 4h 数据: {symbol}"}

    close = df["close"].values.astype(np.float64)
    high = df["high"].values.astype(np.float64)
    low = df["low"].values.astype(np.float64)
    volume = df["volume"].values.astype(np.float64)

    latest_close = close[-1]

    # 1) VWAP (以最近 30 根 4h = ~5天 为周期)
    lookback = min(30, len(close))
    typical_price = (high[-lookback:] + low[-lookback:] + close[-lookback:]) / 3
    cum_tp_vol = np.cumsum(typical_price * volume[-lookback:])
    cum_vol = np.cumsum(volume[-lookback:])
    vwap = cum_tp_vol[-1] / cum_vol[-1] if cum_vol[-1] > 0 else latest_close

    price_vs_vwap = "above" if latest_close > vwap else "below"
    vwap_distance_pct = (latest_close - vwap) / vwap * 100 if vwap > 0 else 0

    # 2) 量比 (当前成交量 / MA20)
    vol_ma20 = np.mean(volume[-20:]) if len(volume) >= 20 else np.mean(volume)
    volume_ratio = volume[-1] / vol_ma20 if vol_ma20 > 0 else 1.0

    if volume_ratio > 2.0:
        volume_state = "heavy"  # 放量
    elif volume_ratio > 1.3:
        volume_state = "above_avg"
    elif volume_ratio < 0.5:
        volume_state = "shrink"  # 缩量
    elif volume_ratio < 0.7:
        volume_state = "below_avg"
    else:
        volume_state = "normal"

    # 3) OBV 量价背离检测
    obv = talib.OBV(close, volume)
    divergence = _detect_obv_divergence(close, obv)

    return {
        "symbol": symbol,
        "vwap": round(float(vwap), 2),
        "price_vs_vwap": price_vs_vwap,
        "vwap_distance_pct": round(float(vwap_distance_pct), 2),
        "volume_ratio": round(float(volume_ratio), 2),
        "volume_state": volume_state,
        "obv_divergence": divergence,
    }


def _detect_obv_divergence(close: np.ndarray, obv: np.ndarray) -> str:
    """检测 OBV 量价背离 (看最近 10 根 K 线趋势)"""
    lookback = 10
    if len(close) < lookback or len(obv) < lookback:
        return "none"

    price_trend = close[-1] - close[-lookback]
    obv_clean = obv[-lookback:]
    obv_clean = obv_clean[~np.isnan(obv_clean)]
    if len(obv_clean) < 2:
        return "none"
    obv_trend = obv_clean[-1] - obv_clean[0]

    # 看涨背离: 价格下跌但 OBV 上升
    if price_trend < 0 and obv_trend > 0:
        return "bullish_divergence"
    # 看跌背离: 价格上涨但 OBV 下降
    if price_trend > 0 and obv_trend < 0:
        return "bearish_divergence"
    return "none"
