"""巨鲸钱包追踪 (Whale Alert API, 免费 10 calls/min, 需可选 WHALE_ALERT_API_KEY)

追踪大额加密货币转入/转出交易所，判断抛压或吸筹。
"""

import logging
import os

import httpx

from cryptobot.cache import get_cache, set_cache

logger = logging.getLogger(__name__)

WHALE_ALERT_API = "https://api.whale-alert.io/v1"
CACHE_TTL = 1800  # 30 分钟

# 仅支持主流币
_SUPPORTED_BASES = {"BTC", "ETH"}


def _empty_result(symbol: str = "") -> dict:
    return {
        "symbol": symbol,
        "exchange_inflow_usd": 0,
        "exchange_outflow_usd": 0,
        "net_flow_usd": 0,
        "whale_signal": "neutral",
        "tx_count": 0,
    }


def get_whale_activity(symbol: str) -> dict:
    """获取巨鲸活动信号

    Returns:
        {"symbol": str, "exchange_inflow_usd": float, "exchange_outflow_usd": float,
         "net_flow_usd": float, "whale_signal": "selling_pressure"|"accumulation"|"neutral",
         "tx_count": int}
    """
    base = symbol.replace("USDT", "")
    if base not in _SUPPORTED_BASES:
        return _empty_result(symbol)

    api_key = os.environ.get("WHALE_ALERT_API_KEY", "")
    if not api_key:
        logger.debug("WHALE_ALERT_API_KEY 未设置, 跳过巨鲸追踪")
        return _empty_result(symbol)

    cache_key = f"whale_{base}"
    cached = get_cache("whale", cache_key, CACHE_TTL)
    if cached:
        return cached

    try:
        resp = httpx.get(
            f"{WHALE_ALERT_API}/transactions",
            params={
                "api_key": api_key,
                "min_value": 500000,
                "currency": base.lower(),
                "start": _24h_ago_unix(),
            },
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        logger.warning("Whale Alert API 请求失败 (%s): %s", base, e)
        return _empty_result(symbol)

    transactions = data.get("transactions", [])
    if not transactions:
        result = _empty_result(symbol)
        result["symbol"] = symbol
        set_cache("whale", cache_key, result)
        return result

    exchange_inflow = 0.0
    exchange_outflow = 0.0
    for tx in transactions:
        amount_usd = tx.get("amount_usd", 0) or 0
        from_type = (tx.get("from", {}) or {}).get("owner_type", "")
        to_type = (tx.get("to", {}) or {}).get("owner_type", "")

        if to_type == "exchange" and from_type != "exchange":
            exchange_inflow += amount_usd
        elif from_type == "exchange" and to_type != "exchange":
            exchange_outflow += amount_usd

    net_flow = exchange_inflow - exchange_outflow
    total = exchange_inflow + exchange_outflow

    # 判断信号
    if total > 0 and exchange_inflow / total > 0.65:
        whale_signal = "selling_pressure"
    elif total > 0 and exchange_outflow / total > 0.65:
        whale_signal = "accumulation"
    else:
        whale_signal = "neutral"

    result = {
        "symbol": symbol,
        "exchange_inflow_usd": round(exchange_inflow, 0),
        "exchange_outflow_usd": round(exchange_outflow, 0),
        "net_flow_usd": round(net_flow, 0),
        "whale_signal": whale_signal,
        "tx_count": len(transactions),
    }
    set_cache("whale", cache_key, result)
    return result


def _24h_ago_unix() -> int:
    """返回 24 小时前的 Unix 时间戳"""
    import time
    return int(time.time()) - 86400
