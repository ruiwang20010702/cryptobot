"""技术指标计算器

从 Freqtrade 数据目录读取 K 线数据，无本地文件时回退到 Binance API。
使用 TA-Lib 计算技术指标。
"""

import logging
import math
from contextlib import contextmanager

import numpy as np
import pandas as pd
import talib

from cryptobot.config import FREQTRADE_DATA_DIR, FREQTRADE_DATA_DIR_ALT

logger = logging.getLogger(__name__)

BINANCE_FAPI = "https://fapi.binance.com"

# ─── 历史模拟: K 线数据覆盖 ──────────────────────────────────────────────

_klines_override: dict[tuple[str, str], pd.DataFrame] | None = None


@contextmanager
def klines_override(cache: dict[tuple[str, str], pd.DataFrame]):
    """临时覆盖 load_klines 数据源，用于历史模拟"""
    global _klines_override
    _klines_override = cache
    try:
        yield
    finally:
        _klines_override = None

# 缓存 TTL (秒): 不同时间框架不同间隔
_CACHE_TTL = {
    "1h": 1800,   # 30 min
    "4h": 7200,   # 2h
    "1d": 14400,  # 4h
}


def _fetch_klines_from_api(
    symbol: str, timeframe: str, limit: int = 200,
) -> pd.DataFrame:
    """从 Binance 公开端点获取 K 线数据，带缓存"""
    import httpx
    from cryptobot.cache import get_cache, set_cache

    cache_key = f"klines_{symbol}_{timeframe}"
    ttl = _CACHE_TTL.get(timeframe, 3600)

    cached = get_cache("klines", cache_key, ttl)
    if cached and "records" in cached:
        df = pd.DataFrame(cached["records"])
        for col in ("open", "high", "low", "close", "volume"):
            df[col] = df[col].astype(float)
        df["datetime"] = pd.to_datetime(df["datetime"], unit="ms")
        df = df.set_index("datetime")
        return df

    resp = httpx.get(
        f"{BINANCE_FAPI}/fapi/v1/klines",
        params={"symbol": symbol, "interval": timeframe, "limit": limit},
        timeout=15,
    )
    resp.raise_for_status()
    raw = resp.json()

    records = [
        {
            "datetime": k[0],
            "open": float(k[1]),
            "high": float(k[2]),
            "low": float(k[3]),
            "close": float(k[4]),
            "volume": float(k[5]),
        }
        for k in raw
    ]

    set_cache("klines", cache_key, {"records": records})

    df = pd.DataFrame(records)
    df["datetime"] = pd.to_datetime(df["datetime"], unit="ms")
    df = df.set_index("datetime")
    return df


def download_klines(
    symbol: str, timeframe: str, limit: int = 500,
    end_time: int | None = None,
) -> pd.DataFrame:
    """从 Binance API 下载历史 K 线（不走缓存，用于历史模拟）

    Args:
        end_time: 结束时间戳（毫秒），用于获取特定时间之前的 K 线
    """
    import httpx

    params = {"symbol": symbol, "interval": timeframe, "limit": limit}
    if end_time is not None:
        params["endTime"] = end_time

    resp = httpx.get(
        f"{BINANCE_FAPI}/fapi/v1/klines",
        params=params,
        timeout=15,
    )
    resp.raise_for_status()
    raw = resp.json()

    records = [
        {
            "datetime": k[0],
            "open": float(k[1]),
            "high": float(k[2]),
            "low": float(k[3]),
            "close": float(k[4]),
            "volume": float(k[5]),
        }
        for k in raw
    ]
    df = pd.DataFrame(records)
    df["datetime"] = pd.to_datetime(df["datetime"], unit="ms")
    df = df.set_index("datetime")
    return df


def load_klines(symbol: str = "BTCUSDT", timeframe: str = "4h") -> pd.DataFrame:
    """加载 K 线数据: 优先 override → 本地 feather → Binance API"""
    if _klines_override and (symbol, timeframe) in _klines_override:
        return _klines_override[(symbol, timeframe)]

    # 1) 尝试本地 Freqtrade feather 文件
    base = symbol.replace("USDT", "")
    filename = f"{base}_USDT_USDT-{timeframe}-futures.feather"
    path = FREQTRADE_DATA_DIR / filename

    if not path.exists():
        path = FREQTRADE_DATA_DIR_ALT / filename

    if path.exists():
        # P12: 检查文件修改时间，超过 6 小时认为过期
        import time as _time
        file_age_hours = (_time.time() - path.stat().st_mtime) / 3600
        if file_age_hours > 6:
            logger.warning("本地 %s 数据过期 (%.1fh)，从 API 获取", symbol, file_age_hours)
        else:
            df = pd.read_feather(path)
            df = df.rename(columns={"date": "datetime"})
            df = df.set_index("datetime")
            return df

    # 2) 回退: 从 Binance API 获取
    try:
        logger.info("本地无 %s %s 数据，从 Binance API 获取", symbol, timeframe)
        return _fetch_klines_from_api(symbol, timeframe)
    except Exception as e:
        raise FileNotFoundError(
            f"K线数据不可用: {symbol} {timeframe}\n"
            f"本地文件不存在，API 获取失败: {e}"
        ) from e


def calc_all_indicators(
    symbol: str = "BTCUSDT", timeframe: str = "4h", regime: str = "trending",
) -> dict:
    """计算全部技术指标，返回最新值的字典"""
    df = load_klines(symbol, timeframe)

    close = df["close"].values.astype(np.float64)
    high = df["high"].values.astype(np.float64)
    low = df["low"].values.astype(np.float64)
    volume = df["volume"].values.astype(np.float64)

    # --- 趋势指标 ---
    ema_7 = talib.EMA(close, timeperiod=7)
    ema_25 = talib.EMA(close, timeperiod=25)
    ema_99 = talib.EMA(close, timeperiod=99)

    macd, macd_signal, macd_hist = talib.MACD(close, fastperiod=12, slowperiod=26, signalperiod=9)

    adx = talib.ADX(high, low, close, timeperiod=14)
    di_plus = talib.PLUS_DI(high, low, close, timeperiod=14)
    di_minus = talib.MINUS_DI(high, low, close, timeperiod=14)

    # --- 动量指标 ---
    rsi = talib.RSI(close, timeperiod=14)

    stochrsi_k, stochrsi_d = talib.STOCHRSI(close, timeperiod=14, fastk_period=3, fastd_period=3)

    cci = talib.CCI(high, low, close, timeperiod=20)

    willr = talib.WILLR(high, low, close, timeperiod=14)

    mfi = talib.MFI(high, low, close, volume, timeperiod=14)

    # --- 波动率指标 ---
    bb_upper, bb_middle, bb_lower = talib.BBANDS(close, timeperiod=20, nbdevup=2, nbdevdn=2)

    atr = talib.ATR(high, low, close, timeperiod=14)

    # --- 成交量指标 ---
    obv = talib.OBV(close, volume)

    # 取最新值
    latest_close = close[-1]
    latest_volume = volume[-1]

    e7 = _safe(ema_7[-1])
    e25 = _safe(ema_25[-1])
    e99 = _safe(ema_99[-1])

    macd_val = _safe(macd[-1])
    macd_sig = _safe(macd_signal[-1])
    macd_h = _safe(macd_hist[-1])
    macd_h_prev = _safe(macd_hist[-2]) if len(macd_hist) > 1 else None

    rsi_val = _safe(rsi[-1])

    bb_u = _safe(bb_upper[-1])
    bb_m = _safe(bb_middle[-1])
    bb_l = _safe(bb_lower[-1])
    atr_val = _safe(atr[-1])

    # 派生计算
    ema_align = _ema_alignment(e7, e25, e99)
    cross = _macd_cross(macd_h, macd_h_prev)
    bb_w = _bb_width(bb_u, bb_m, bb_l)
    bb_p = _bb_position(bb_u, bb_l, latest_close)
    atr_pct = atr_val / latest_close * 100 if atr_val and latest_close else 0

    result = {
        "symbol": symbol,
        "timeframe": timeframe,
        "kline_count": len(df),
        "latest_close": latest_close,
        "latest_time": str(df.index[-1]),
        "trend": {
            "ema_7": e7,
            "ema_25": e25,
            "ema_99": e99,
            "ema_alignment": ema_align,
            "macd": macd_val,
            "macd_signal": macd_sig,
            "macd_hist": macd_h,
            "macd_cross": cross,
            "adx": _safe(adx[-1]),
            "di_plus": _safe(di_plus[-1]),
            "di_minus": _safe(di_minus[-1]),
        },
        "momentum": {
            "rsi_14": rsi_val,
            "rsi_zone": _rsi_zone(rsi_val),
            "stochrsi_k": _safe(stochrsi_k[-1]),
            "stochrsi_d": _safe(stochrsi_d[-1]),
            "cci_20": _safe(cci[-1]),
            "willr_14": _safe(willr[-1]),
            "mfi_14": _safe(mfi[-1]),
        },
        "volatility": {
            "bb_upper": bb_u,
            "bb_middle": bb_m,
            "bb_lower": bb_l,
            "bb_width": bb_w,
            "bb_position": bb_p,
            "atr_14": atr_val,
            "atr_pct": round(atr_pct, 4),
        },
        "volume": {
            "obv": _safe(obv[-1]),
            "volume_latest": latest_volume,
        },
        "signals": _generate_signals(
            rsi=rsi_val,
            macd_cross=cross,
            macd_hist=macd_h,
            ema_alignment=ema_align,
            bb_position=bb_p,
            adx=_safe(adx[-1]),
            mfi=_safe(mfi[-1]),
            regime=regime,
        ),
    }
    return result


# ─── 辅助函数 ────────────────────────────────────────────────────────────

def _safe(val) -> float | None:
    """将 numpy 值转为 Python float，NaN → None"""
    if val is None or (isinstance(val, float) and math.isnan(val)):
        return None
    try:
        f = float(val)
        return None if math.isnan(f) else round(f, 4)
    except (TypeError, ValueError):
        return None


def _ema_alignment(e7, e25, e99) -> str:
    """EMA 排列状态（P22: 增加间距阈值避免震荡市假信号）"""
    if None in (e7, e25, e99):
        return "unknown"
    gap_7_25 = abs(e7 - e25) / e25 * 100 if e25 else 0
    gap_25_99 = abs(e25 - e99) / e99 * 100 if e99 else 0
    min_gap = 0.1  # 最小间距 0.1%
    if e7 > e25 > e99 and gap_7_25 > min_gap and gap_25_99 > min_gap:
        return "bullish"  # 多头排列
    elif e7 < e25 < e99 and gap_7_25 > min_gap and gap_25_99 > min_gap:
        return "bearish"  # 空头排列
    return "mixed"


def _macd_cross(curr_hist, prev_hist) -> str:
    """MACD 交叉信号"""
    if curr_hist is None or prev_hist is None:
        return "none"
    if prev_hist <= 0 < curr_hist:
        return "golden_cross"  # 金叉
    elif prev_hist >= 0 > curr_hist:
        return "death_cross"  # 死叉
    return "none"


def _rsi_zone(rsi: float | None) -> str:
    if rsi is None:
        return "unknown"
    if rsi > 70:
        return "overbought"
    elif rsi < 30:
        return "oversold"
    elif rsi > 60:
        return "strong"
    elif rsi < 40:
        return "weak"
    return "neutral"


def _bb_width(upper, middle, lower) -> float | None:
    if None in (upper, lower, middle) or middle == 0:
        return None
    return round((upper - lower) / middle * 100, 4)


def _bb_position(upper, lower, close) -> float | None:
    """价格在布林带中的位置 (0=下轨, 1=上轨)"""
    if None in (upper, lower) or upper == lower:
        return None
    return round((close - lower) / (upper - lower), 4)


def _generate_signals(
    *,
    rsi,
    macd_cross,
    macd_hist,
    ema_alignment,
    bb_position,
    adx,
    mfi,
    regime="trending",
) -> dict:
    """综合信号判断（P18: regime 感知权重）"""
    # regime 权重乘数
    _WEIGHT_MODS = {
        "trending": {"rsi": 0.7, "macd": 1.0, "ema": 1.3, "bb": 0.8},
        "ranging":  {"rsi": 1.2, "macd": 0.8, "ema": 0.6, "bb": 1.3},
        "volatile": {"rsi": 0.5, "macd": 0.7, "ema": 0.8, "bb": 1.0},
    }
    w = _WEIGHT_MODS.get(regime, _WEIGHT_MODS["trending"])

    signals = []
    score = 0  # -10 到 +10

    # RSI 信号
    if rsi:
        if rsi > 70:
            signals.append("RSI 超买")
            score -= 1.5 * w["rsi"]
        elif rsi < 30:
            signals.append("RSI 超卖")
            score += 1.5 * w["rsi"]
        elif rsi > 50:
            score += 0.5 * w["rsi"]
        else:
            score -= 0.5 * w["rsi"]

    # MACD 信号
    if macd_cross == "golden_cross":
        signals.append("MACD 金叉")
        score += 2 * w["macd"]
    elif macd_cross == "death_cross":
        signals.append("MACD 死叉")
        score -= 2 * w["macd"]
    if macd_hist and macd_hist > 0:
        score += 0.5 * w["macd"]
    elif macd_hist and macd_hist < 0:
        score -= 0.5 * w["macd"]

    # EMA 趋势
    if ema_alignment == "bullish":
        signals.append("EMA 多头排列")
        score += 2 * w["ema"]
    elif ema_alignment == "bearish":
        signals.append("EMA 空头排列")
        score -= 2 * w["ema"]

    # 布林带位置
    if bb_position is not None:
        if bb_position > 0.95:
            signals.append("触及布林上轨")
            score -= 1 * w["bb"]
        elif bb_position < 0.05:
            signals.append("触及布林下轨")
            score += 1 * w["bb"]

    # ADX 趋势强度
    if adx and adx > 25:
        signals.append(f"趋势较强 (ADX={adx:.0f})")

    # MFI
    if mfi:
        if mfi > 80:
            signals.append("MFI 超买")
            score -= 1
        elif mfi < 20:
            signals.append("MFI 超卖")
            score += 1

    # 总评
    score = max(-10, min(10, score))
    if score > 2:
        bias = "bullish"
    elif score < -2:
        bias = "bearish"
    else:
        bias = "neutral"

    return {
        "technical_score": round(score, 1),
        "bias": bias,
        "signals": signals,
    }
