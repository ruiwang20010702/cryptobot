"""代币稀释风险评估

复用 CoinGecko 已有数据 (data/news.py:get_coin_info) 计算供应量稀释比例。
"""

import logging

from cryptobot.cache import get_cache, set_cache

logger = logging.getLogger(__name__)

CACHE_TTL = 7200  # 2 小时


def _empty_result(symbol: str) -> dict:
    return {
        "symbol": symbol,
        "circulating_supply": 0,
        "total_supply": 0,
        "dilution_pct": 0,
        "risk_level": "unknown",
        "label": "数据不可用",
    }


def get_dilution_risk(symbol: str) -> dict:
    """评估代币稀释风险

    Args:
        symbol: 交易对如 "BTCUSDT"

    Returns:
        {"symbol", "circulating_supply", "total_supply", "dilution_pct", "risk_level", "label"}
    """
    base = symbol.replace("USDT", "")
    cache_key = f"dilution_{base}"
    cached = get_cache("dilution", cache_key, CACHE_TTL)
    if cached:
        return cached

    try:
        from cryptobot.data.news import get_coin_info

        coin = get_coin_info(base)
    except Exception as e:
        logger.warning("获取 %s 供应数据失败: %s", base, e)
        return _empty_result(base)

    if "error" in coin:
        return _empty_result(base)

    circ = coin.get("circulating_supply", 0) or 0
    total = coin.get("total_supply", 0) or 0

    if circ <= 0 or total <= 0:
        result = {
            "symbol": base,
            "circulating_supply": circ,
            "total_supply": total,
            "dilution_pct": 0,
            "risk_level": "none",
            "label": "供应数据不可用",
        }
        set_cache("dilution", cache_key, result)
        return result

    dilution_pct = (total - circ) / circ * 100

    if dilution_pct <= 0.01:  # 接近全流通
        risk_level = "none"
        label = "全流通，无稀释风险"
    elif dilution_pct < 50:
        risk_level = "low"
        label = f"稀释风险低 ({dilution_pct:.1f}%)"
    elif dilution_pct < 100:
        risk_level = "medium"
        label = f"稀释风险中等 ({dilution_pct:.1f}%)"
    else:
        risk_level = "high"
        label = f"稀释风险高 ({dilution_pct:.1f}%)"

    result = {
        "symbol": base,
        "circulating_supply": circ,
        "total_supply": total,
        "dilution_pct": round(dilution_pct, 1),
        "risk_level": risk_level,
        "label": label,
    }
    set_cache("dilution", cache_key, result)
    return result
