"""CoinGlass 清算热力图数据"""

import logging
import os

import httpx

from cryptobot.cache import get_cache, set_cache
from cryptobot.config import load_settings

logger = logging.getLogger(__name__)

CACHE_TTL = 1800  # 30 分钟


def _empty_result(symbol: str) -> dict:
    return {
        "symbol": symbol,
        "long_liq_usd": 0,
        "short_liq_usd": 0,
        "liq_ratio": 0,
        "nearest_liq_level": "unknown",
        "data_source": "coinglass",
    }


def _calc_nearest_liq_level(long_liq: float, short_liq: float) -> str:
    """判断清算密集方向

    - 空头清算多 (空头在上方被清) → "above"
    - 多头清算多 (多头在下方被清) → "below"
    - 差异不大 → "balanced"
    """
    if long_liq == 0 and short_liq == 0:
        return "balanced"
    total = long_liq + short_liq
    if total == 0:
        return "balanced"
    ratio = abs(long_liq - short_liq) / total
    if ratio < 0.2:
        return "balanced"
    if short_liq > long_liq:
        return "above"
    return "below"


def get_liquidation_heatmap(symbol: str = "BTCUSDT") -> dict:
    """获取 CoinGlass 清算数据，识别上下方清算密集区

    Returns:
        {"symbol": str, "long_liq_usd": float, "short_liq_usd": float,
         "liq_ratio": float, "nearest_liq_level": str, "data_source": "coinglass"}
    """
    cache_key = f"coinglass_liq_{symbol}"
    cached = get_cache("coinglass", cache_key, CACHE_TTL)
    if cached:
        return cached

    api_key = os.environ.get("COINGLASS_API_KEY", "")
    if not api_key:
        logger.warning("COINGLASS_API_KEY 未设置，跳过 CoinGlass 数据")
        return _empty_result(symbol)

    settings = load_settings()
    base_url = settings.get("data_sources", {}).get("coinglass", {}).get(
        "base_url", "https://open-api-v3.coinglass.com"
    )

    # 去掉 USDT 后缀作为 symbol 参数
    sym = symbol.replace("USDT", "") if symbol.endswith("USDT") else symbol

    try:
        resp = httpx.get(
            f"{base_url}/api/pro/v1/futures/liquidation/info",
            params={"symbol": sym, "timeType": "2"},
            headers={"coinglassSecret": api_key},
            timeout=10,
        )
        resp.raise_for_status()
        body = resp.json()
    except Exception as e:
        logger.warning("CoinGlass API 请求失败 %s: %s", symbol, e)
        return _empty_result(symbol)

    if body.get("code") != "0" or not body.get("data"):
        logger.warning("CoinGlass 响应异常 %s: code=%s", symbol, body.get("code"))
        return _empty_result(symbol)

    data = body["data"]
    long_liq_usd = float(data.get("longLiqUsd", 0) or 0)
    short_liq_usd = float(data.get("shortLiqUsd", 0) or 0)

    if short_liq_usd == 0:
        liq_ratio = 999.0 if long_liq_usd > 0 else 0.0
    else:
        liq_ratio = round(long_liq_usd / short_liq_usd, 4)

    nearest_liq_level = _calc_nearest_liq_level(long_liq_usd, short_liq_usd)

    result = {
        "symbol": symbol,
        "long_liq_usd": long_liq_usd,
        "short_liq_usd": short_liq_usd,
        "liq_ratio": liq_ratio,
        "nearest_liq_level": nearest_liq_level,
        "data_source": "coinglass",
    }
    set_cache("coinglass", cache_key, result)
    return result
