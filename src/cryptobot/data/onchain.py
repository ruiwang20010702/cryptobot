"""链上数据获取 (Binance 免费公开 API)

可用数据:
- 资金费率历史 (funding rate)
- 持仓量历史 (open interest)
- 主动买卖比 (taker buy/sell ratio)
"""

import logging

import httpx

from cryptobot.cache import get_cache, set_cache

logger = logging.getLogger(__name__)

BINANCE_FAPI = "https://fapi.binance.com"
CACHE_TTL = 900  # 15 分钟


def get_funding_rate(symbol: str = "BTCUSDT", limit: int = 100) -> dict:
    """获取资金费率历史"""
    cache_key = f"funding_rate_{symbol}"
    cached = get_cache("onchain", cache_key, CACHE_TTL)
    if cached:
        return cached

    try:
        resp = httpx.get(
            f"{BINANCE_FAPI}/fapi/v1/fundingRate",
            params={"symbol": symbol, "limit": limit},
            timeout=10,
        )
        resp.raise_for_status()
        raw = resp.json()
    except Exception as e:
        logger.warning("获取资金费率失败 %s: %s", symbol, e)
        return {"symbol": symbol, "current_rate": 0, "error": str(e), "data_available": False}

    rates = [
        {
            "time": r["fundingTime"],
            "rate": float(r["fundingRate"]),
            "mark_price": float(r.get("markPrice", 0)),
        }
        for r in raw
    ]

    # 统计
    recent_rates = [r["rate"] for r in rates[-30:]]  # 最近 30 条 (~10天)
    result = {
        "symbol": symbol,
        "rates": rates,
        "current_rate": rates[-1]["rate"] if rates else 0,
        "avg_rate_30": sum(recent_rates) / len(recent_rates) if recent_rates else 0,
        "max_rate_30": max(recent_rates) if recent_rates else 0,
        "min_rate_30": min(recent_rates) if recent_rates else 0,
        "positive_count": sum(1 for r in recent_rates if r > 0),
        "negative_count": sum(1 for r in recent_rates if r < 0),
        "count": len(rates),
    }
    set_cache("onchain", cache_key, result)
    return result


def get_open_interest_hist(
    symbol: str = "BTCUSDT", period: str = "1h", limit: int = 48
) -> dict:
    """获取持仓量历史 (OI)"""
    cache_key = f"oi_hist_{symbol}_{period}"
    cached = get_cache("onchain", cache_key, CACHE_TTL)
    if cached:
        return cached

    try:
        resp = httpx.get(
            f"{BINANCE_FAPI}/futures/data/openInterestHist",
            params={"symbol": symbol, "period": period, "limit": limit},
            timeout=10,
        )
        resp.raise_for_status()
        raw = resp.json()
    except Exception as e:
        logger.warning("获取持仓量历史失败 %s: %s", symbol, e)
        return {"symbol": symbol, "oi_change_pct": 0, "error": str(e), "data_available": False}

    records = [
        {
            "time": r["timestamp"],
            "oi": float(r["sumOpenInterest"]),
            "oi_value": float(r["sumOpenInterestValue"]),
        }
        for r in raw
    ]

    oi_values = [r["oi_value"] for r in records]
    result = {
        "symbol": symbol,
        "period": period,
        "records": records,
        "current_oi_value": oi_values[-1] if oi_values else 0,
        "max_oi_value": max(oi_values) if oi_values else 0,
        "min_oi_value": min(oi_values) if oi_values else 0,
        "oi_change_pct": (
            (oi_values[-1] - oi_values[0]) / oi_values[0] * 100
            if len(oi_values) >= 2 and oi_values[0] > 0
            else 0
        ),
        "count": len(records),
    }
    set_cache("onchain", cache_key, result)
    return result


def get_taker_buy_sell_ratio(
    symbol: str = "BTCUSDT", period: str = "1h", limit: int = 48
) -> dict:
    """获取主动买卖比 (Taker Buy/Sell Volume Ratio)"""
    cache_key = f"taker_ratio_{symbol}_{period}"
    cached = get_cache("onchain", cache_key, CACHE_TTL)
    if cached:
        return cached

    try:
        resp = httpx.get(
            f"{BINANCE_FAPI}/futures/data/takerlongshortRatio",
            params={"symbol": symbol, "period": period, "limit": limit},
            timeout=10,
        )
        resp.raise_for_status()
        raw = resp.json()
    except Exception as e:
        logger.warning("获取主动买卖比失败 %s: %s", symbol, e)
        return {"symbol": symbol, "current_ratio": 1.0, "error": str(e), "data_available": False}

    records = [
        {
            "time": r["timestamp"],
            "buy_sell_ratio": float(r["buySellRatio"]),
            "buy_vol": float(r["buyVol"]),
            "sell_vol": float(r["sellVol"]),
        }
        for r in raw
    ]

    ratios = [r["buy_sell_ratio"] for r in records]
    result = {
        "symbol": symbol,
        "period": period,
        "records": records,
        "current_ratio": ratios[-1] if ratios else 1.0,
        "avg_ratio": sum(ratios) / len(ratios) if ratios else 1.0,
        "bullish_count": sum(1 for r in ratios if r > 1.0),
        "bearish_count": sum(1 for r in ratios if r < 1.0),
        "count": len(records),
    }
    set_cache("onchain", cache_key, result)
    return result
