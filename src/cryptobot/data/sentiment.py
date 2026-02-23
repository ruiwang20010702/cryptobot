"""情绪数据获取

数据源:
- Fear & Greed Index: Alternative.me (免费)
- 多空比: Binance 公开 API (免费)
- 大户多空比: Binance 公开 API (免费)
"""

import logging

import httpx

from cryptobot.cache import get_cache, set_cache

logger = logging.getLogger(__name__)

BINANCE_FAPI = "https://fapi.binance.com"
FEAR_GREED_API = "https://api.alternative.me/fng"
CACHE_TTL_SENTIMENT = 3600  # 恐贪指数 1 小时缓存
CACHE_TTL_RATIO = 900  # 多空比 15 分钟缓存


def get_fear_greed_index(limit: int = 30) -> dict:
    """获取恐惧贪婪指数"""
    cache_key = "fear_greed"
    cached = get_cache("sentiment", cache_key, CACHE_TTL_SENTIMENT)
    if cached:
        return cached

    try:
        resp = httpx.get(
            FEAR_GREED_API,
            params={"limit": limit, "format": "json"},
            timeout=10,
            follow_redirects=True,
        )
        resp.raise_for_status()
        raw = resp.json()
    except Exception as e:
        logger.warning("获取恐惧贪婪指数失败: %s", e)
        return {
            "current_value": 50, "current_classification": "Neutral",
            "avg_7d": None, "avg_30d": None, "trend": None,
            "error": str(e), "data_available": False,
        }

    records = [
        {
            "value": int(r["value"]),
            "classification": r["value_classification"],
            "timestamp": int(r["timestamp"]),
        }
        for r in raw.get("data", [])
    ]

    current = records[0] if records else {"value": 50, "classification": "Neutral"}
    values = [r["value"] for r in records]

    result = {
        "current_value": current["value"],
        "current_classification": current["classification"],
        "records": records,
        "avg_7d": sum(values[:7]) / min(len(values), 7) if values else 50,
        "avg_30d": sum(values[:30]) / min(len(values), 30) if values else 50,
        "trend": _classify_trend(values[:7]) if len(values) >= 3 else "neutral",
        "count": len(records),
    }
    set_cache("sentiment", cache_key, result)
    return result


def _classify_trend(values: list[int]) -> str:
    """判断恐贪指数趋势"""
    if len(values) < 3:
        return "neutral"
    recent = sum(values[:3]) / 3
    older = sum(values[3:min(7, len(values))]) / max(len(values[3:7]), 1)
    diff = recent - older
    if diff > 5:
        return "rising"  # 趋向贪婪
    elif diff < -5:
        return "falling"  # 趋向恐惧
    return "neutral"


def calc_realtime_sentiment(
    derivatives: dict | None, fg_value: int | None,
) -> dict:
    """合成实时情绪指标 (补充 Fear&Greed 的延迟)

    Args:
        derivatives: 衍生品数据 (funding_rate, long_short_ratio 等)
        fg_value: Fear&Greed 指数值 (0-100)

    Returns:
        {"sentiment_score": int, "sources": [str]}
    """
    score = 50.0  # 中性基准
    sources = []

    if fg_value is not None:
        score = fg_value * 0.4 + score * 0.6
        sources.append(f"FG={fg_value}")

    if derivatives:
        funding = derivatives.get("funding_rate", 0) or 0
        if funding > 0.01:
            score += 10
        elif funding < -0.01:
            score -= 10

        ls_ratio = derivatives.get("long_short_ratio", 1.0) or 1.0
        if ls_ratio > 1.5:
            score += 5
        elif ls_ratio < 0.7:
            score -= 5
        sources.append(f"funding={funding:.4f}, ls={ls_ratio:.2f}")

    return {"sentiment_score": round(max(0, min(100, score))), "sources": sources}


def get_long_short_ratio(
    symbol: str = "BTCUSDT", period: str = "1h", limit: int = 30
) -> dict:
    """获取全网多空比"""
    cache_key = f"ls_ratio_{symbol}_{period}"
    cached = get_cache("sentiment", cache_key, CACHE_TTL_RATIO)
    if cached:
        return cached

    try:
        resp = httpx.get(
            f"{BINANCE_FAPI}/futures/data/globalLongShortAccountRatio",
            params={"symbol": symbol, "period": period, "limit": limit},
            timeout=10,
        )
        resp.raise_for_status()
        raw = resp.json()
    except Exception as e:
        logger.warning("获取多空比失败 %s: %s", symbol, e)
        return {
            "symbol": symbol, "current_ratio": 1.0,
            "error": str(e), "data_available": False,
        }

    records = [
        {
            "time": r["timestamp"],
            "long_short_ratio": float(r["longShortRatio"]),
            "long_account": float(r["longAccount"]),
            "short_account": float(r["shortAccount"]),
        }
        for r in raw
    ]

    ratios = [r["long_short_ratio"] for r in records]
    result = {
        "symbol": symbol,
        "period": period,
        "records": records,
        "current_ratio": ratios[-1] if ratios else 1.0,
        "current_long_pct": records[-1]["long_account"] * 100 if records else 50,
        "current_short_pct": records[-1]["short_account"] * 100 if records else 50,
        "avg_ratio": sum(ratios) / len(ratios) if ratios else 1.0,
        "max_ratio": max(ratios) if ratios else 1.0,
        "min_ratio": min(ratios) if ratios else 1.0,
        "count": len(records),
    }
    set_cache("sentiment", cache_key, result)
    return result


def get_top_trader_long_short(
    symbol: str = "BTCUSDT", period: str = "1h", limit: int = 30
) -> dict:
    """获取大户多空比 (账户数)"""
    cache_key = f"top_ls_{symbol}_{period}"
    cached = get_cache("sentiment", cache_key, CACHE_TTL_RATIO)
    if cached:
        return cached

    try:
        resp = httpx.get(
            f"{BINANCE_FAPI}/futures/data/topLongShortAccountRatio",
            params={"symbol": symbol, "period": period, "limit": limit},
            timeout=10,
        )
        resp.raise_for_status()
        raw = resp.json()
    except Exception as e:
        logger.warning("获取大户多空比失败 %s: %s", symbol, e)
        return {
            "symbol": symbol, "current_ratio": 1.0,
            "error": str(e), "data_available": False,
        }

    records = [
        {
            "time": r["timestamp"],
            "long_short_ratio": float(r["longShortRatio"]),
            "long_account": float(r["longAccount"]),
            "short_account": float(r["shortAccount"]),
        }
        for r in raw
    ]

    ratios = [r["long_short_ratio"] for r in records]
    result = {
        "symbol": symbol,
        "period": period,
        "records": records,
        "current_ratio": ratios[-1] if ratios else 1.0,
        "current_long_pct": records[-1]["long_account"] * 100 if records else 50,
        "current_short_pct": records[-1]["short_account"] * 100 if records else 50,
        "avg_ratio": sum(ratios) / len(ratios) if ratios else 1.0,
        "count": len(records),
    }
    set_cache("sentiment", cache_key, result)
    return result
