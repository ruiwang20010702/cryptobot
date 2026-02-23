"""订单簿深度数据 (Binance 公开 API, 无需 key)"""

import logging

import httpx

from cryptobot.cache import get_cache, set_cache

logger = logging.getLogger(__name__)

BINANCE_FAPI = "https://fapi.binance.com"
CACHE_TTL = 300  # 5 分钟 (订单簿变化快)
EMPTY_CACHE_TTL = 60  # 空返回短 TTL，防止 API 雪崩


def _empty_result() -> dict:
    """返回零值结果"""
    return {
        "bid_volume": 0.0,
        "ask_volume": 0.0,
        "bid_ask_ratio": 0.0,
        "top_bid": 0.0,
        "top_ask": 0.0,
        "spread_pct": 0.0,
        "data_available": False,
    }


def get_orderbook_depth(symbol: str = "BTCUSDT", limit: int = 20) -> dict:
    """获取订单簿深度，计算买卖压力比

    Returns:
        {"bid_volume": float, "ask_volume": float, "bid_ask_ratio": float,
         "top_bid": float, "top_ask": float, "spread_pct": float}
    """
    cache_key = f"orderbook_{symbol}"
    cached = get_cache("orderbook", cache_key, CACHE_TTL)
    if cached:
        return cached

    try:
        resp = httpx.get(
            f"{BINANCE_FAPI}/fapi/v1/depth",
            params={"symbol": symbol, "limit": limit},
            timeout=10,
        )
        resp.raise_for_status()
        raw = resp.json()

        bids = raw.get("bids", [])
        asks = raw.get("asks", [])

        if not bids or not asks:
            empty = _empty_result()
            set_cache("orderbook", cache_key, empty)
            return empty

        bid_volume = sum(float(qty) for _price, qty in bids)
        ask_volume = sum(float(qty) for _price, qty in asks)
        bid_ask_ratio = bid_volume / ask_volume if ask_volume > 0 else 999.0

        top_bid = float(bids[0][0])
        top_ask = float(asks[0][0])
        spread_pct = (top_ask - top_bid) / top_bid * 100 if top_bid > 0 else 0.0

        result = {
            "bid_volume": round(bid_volume, 4),
            "ask_volume": round(ask_volume, 4),
            "bid_ask_ratio": round(bid_ask_ratio, 4),
            "top_bid": top_bid,
            "top_ask": top_ask,
            "spread_pct": round(spread_pct, 6),
        }
        set_cache("orderbook", cache_key, result)
        return result

    except Exception as e:
        logger.warning("订单簿获取失败 %s: %s", symbol, e)
        empty = _empty_result()
        # 短 TTL 缓存空结果，防止频繁重试导致 API 雪崩
        set_cache("orderbook", cache_key, empty)
        return empty
