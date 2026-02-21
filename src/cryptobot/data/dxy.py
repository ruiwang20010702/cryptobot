"""DXY 美元指数数据 (Yahoo Finance 公开 Chart API, 无需 key)

DXY 上涨 → 利空 crypto，下跌 → 利多 crypto。
"""

import logging

import httpx

from cryptobot.cache import get_cache, set_cache

logger = logging.getLogger(__name__)

YAHOO_CHART_API = "https://query1.finance.yahoo.com/v8/finance/chart/DX-Y.NYB"
CACHE_TTL = 3600  # 1 小时
STALE_CACHE_TTL = 7 * 24 * 3600  # 7 天过期缓存作为兜底


def _empty_result(data_available: bool = True) -> dict:
    return {
        "current_value": 0,
        "change_1d_pct": 0,
        "change_7d_pct": 0,
        "trend": "stable",
        "signal": "neutral",
        "_data_available": data_available,
    }


def _stale_fallback(cache_key: str) -> dict:
    """失败时尝试返回过期缓存 (7 天内)"""
    stale = get_cache("dxy", cache_key, STALE_CACHE_TTL)
    if stale:
        logger.info("DXY 使用过期缓存数据")
        return {**stale, "_is_stale": True}
    return _empty_result(data_available=False)


def get_dxy_trend() -> dict:
    """获取 DXY 美元指数趋势及对 crypto 的影响

    Returns:
        {"current_value": float, "change_1d_pct": float, "change_7d_pct": float,
         "trend": "strengthening"|"weakening"|"stable",
         "signal": "bearish"|"bullish"|"neutral"}
    """
    cache_key = "dxy_trend"
    cached = get_cache("dxy", cache_key, CACHE_TTL)
    if cached:
        return cached

    try:
        resp = httpx.get(
            YAHOO_CHART_API,
            params={"interval": "1d", "range": "7d"},
            headers={"User-Agent": "Mozilla/5.0 CryptoBot/1.0"},
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        logger.warning("Yahoo Finance DXY 请求失败: %s", e)
        return _stale_fallback(cache_key)

    try:
        result_data = data["chart"]["result"][0]
        closes = result_data["indicators"]["quote"][0]["close"]
        # 过滤 None 值
        closes = [c for c in closes if c is not None]
    except (KeyError, IndexError, TypeError) as e:
        logger.warning("DXY 数据解析失败: %s", e)
        return _stale_fallback(cache_key)

    if len(closes) < 2:
        logger.warning("DXY 数据不足 (仅 %d 个数据点)", len(closes))
        return _stale_fallback(cache_key)

    current = closes[-1]
    prev_1d = closes[-2] if len(closes) >= 2 else current
    prev_7d = closes[0]

    change_1d = (current - prev_1d) / prev_1d * 100 if prev_1d > 0 else 0
    change_7d = (current - prev_7d) / prev_7d * 100 if prev_7d > 0 else 0

    # DXY 趋势判断
    if change_1d > 0.3:
        trend = "strengthening"
    elif change_1d < -0.3:
        trend = "weakening"
    else:
        trend = "stable"

    # 对 crypto 的影响 (反向)
    if change_1d > 0.5:
        signal = "bearish"
    elif change_1d < -0.5:
        signal = "bullish"
    else:
        signal = "neutral"

    result = {
        "current_value": round(current, 2),
        "change_1d_pct": round(change_1d, 3),
        "change_7d_pct": round(change_7d, 3),
        "trend": trend,
        "signal": signal,
    }
    set_cache("dxy", cache_key, result)
    return result
