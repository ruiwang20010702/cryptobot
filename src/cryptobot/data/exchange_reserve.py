"""持仓量趋势数据 (CoinGlass API)

获取 BTC/ETH 等主流币 Open Interest 变化趋势。
仅主流币种有数据，其他币种返回空结果。
"""

import logging
import os

import httpx

from cryptobot.cache import get_cache, set_cache
from cryptobot.config import load_settings

logger = logging.getLogger(__name__)

CACHE_TTL = 3600  # 1 小时

# 支持查询 OI 趋势的主流币种
_SUPPORTED_SYMBOLS = {"BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT"}


def _empty_result(symbol: str) -> dict:
    return {
        "symbol": symbol,
        "open_interest": 0,
        "oi_change_7d_pct": 0,
        "oi_trend": "unknown",
        "data_source": "coinglass",
    }


def get_open_interest_trend(symbol: str = "BTCUSDT") -> dict:
    """获取持仓量 (Open Interest) 7 天变化趋势

    Returns:
        {"symbol": str, "open_interest": float, "oi_change_7d_pct": float,
         "oi_trend": "increasing"|"decreasing"|"stable"|"unknown",
         "data_source": "coinglass"}
    """
    if symbol not in _SUPPORTED_SYMBOLS:
        return _empty_result(symbol)

    cache_key = f"exchange_reserve_{symbol}"
    cached = get_cache("exchange_reserve", cache_key, CACHE_TTL)
    if cached:
        return cached

    api_key = os.environ.get("COINGLASS_API_KEY", "")
    if not api_key:
        logger.warning("COINGLASS_API_KEY 未设置，跳过持仓量趋势")
        return _empty_result(symbol)

    settings = load_settings()
    base_url = settings.get("data_sources", {}).get("coinglass", {}).get(
        "base_url", "https://open-api-v3.coinglass.com"
    )

    sym = symbol.replace("USDT", "") if symbol.endswith("USDT") else symbol

    try:
        resp = httpx.get(
            f"{base_url}/api/pro/v1/futures/openInterest/chart",
            params={"symbol": sym, "range": "7d"},
            headers={"coinglassSecret": api_key},
            timeout=10,
        )
        resp.raise_for_status()
        body = resp.json()
    except Exception as e:
        logger.warning("CoinGlass OI 趋势请求失败 %s: %s", symbol, e)
        return _empty_result(symbol)

    if body.get("code") != "0" or not body.get("data"):
        logger.warning("CoinGlass OI 趋势响应异常 %s: code=%s", symbol, body.get("code"))
        return _empty_result(symbol)

    data_list = body["data"]
    if not isinstance(data_list, list) or len(data_list) < 2:
        return _empty_result(symbol)

    # 取最新和最早数据点计算变化
    latest = data_list[-1]
    earliest = data_list[0]

    current_oi = float(latest.get("openInterest", 0) or latest.get("y", 0) or 0)
    earliest_oi = float(earliest.get("openInterest", 0) or earliest.get("y", 0) or 0)

    if earliest_oi > 0:
        change_7d_pct = (current_oi - earliest_oi) / earliest_oi * 100
    else:
        change_7d_pct = 0

    # 判断趋势
    if change_7d_pct > 5:
        oi_trend = "increasing"
    elif change_7d_pct < -5:
        oi_trend = "decreasing"
    else:
        oi_trend = "stable"

    result = {
        "symbol": symbol,
        "open_interest": round(current_oi, 2),
        "oi_change_7d_pct": round(change_7d_pct, 2),
        "oi_trend": oi_trend,
        "data_source": "coinglass",
    }
    set_cache("exchange_reserve", cache_key, result)
    return result
