"""期权市场数据 (Deribit 公开 API, 无需 key)

获取 BTC/ETH 期权 Put/Call 比率和 OI，判断机构预期。
"""

import logging

import httpx

from cryptobot.cache import get_cache, set_cache

logger = logging.getLogger(__name__)

DERIBIT_API = "https://www.deribit.com/api/v2/public"
CACHE_TTL = 1800  # 30 分钟

# 仅支持 BTC/ETH (Deribit 主要市场)
_SUPPORTED = {"BTC", "ETH"}


def _empty_result(symbol: str = "") -> dict:
    return {
        "symbol": symbol,
        "put_oi": 0.0,
        "call_oi": 0.0,
        "put_call_ratio": 0.0,
        "put_call_signal": "neutral",
        "total_volume_24h": 0.0,
        "data_source": "deribit",
    }


def get_options_sentiment(symbol: str) -> dict:
    """获取期权市场情绪 (Put/Call Ratio)

    Args:
        symbol: 交易对如 "BTCUSDT"

    Returns:
        {"symbol", "put_oi", "call_oi", "put_call_ratio",
         "put_call_signal", "total_volume_24h", "data_source"}
    """
    base = symbol.replace("USDT", "").upper()
    if base not in _SUPPORTED:
        return _empty_result(base)

    cache_key = f"options_{base}"
    cached = get_cache("options", cache_key, CACHE_TTL)
    if cached:
        return cached

    try:
        resp = httpx.get(
            f"{DERIBIT_API}/get_book_summary_by_currency",
            params={"currency": base, "kind": "option"},
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        logger.warning("Deribit 期权数据请求失败 %s: %s", base, e)
        return _empty_result(base)

    entries = data.get("result", [])
    if not entries:
        result = _empty_result(base)
        set_cache("options", cache_key, result)
        return result

    put_oi = 0.0
    call_oi = 0.0
    total_volume = 0.0

    for entry in entries:
        name = entry.get("instrument_name", "")
        oi = float(entry.get("open_interest", 0) or 0)
        vol = float(entry.get("volume", 0) or 0)
        total_volume += vol

        if name.endswith("-P") or "-P-" in name:
            put_oi += oi
        elif name.endswith("-C") or "-C-" in name:
            call_oi += oi

    put_call_ratio = put_oi / call_oi if call_oi > 0 else 0.0

    if put_call_ratio > 1.2:
        signal = "bearish"
    elif put_call_ratio < 0.7:
        signal = "bullish"
    else:
        signal = "neutral"

    result = {
        "symbol": base,
        "put_oi": round(put_oi, 2),
        "call_oi": round(call_oi, 2),
        "put_call_ratio": round(put_call_ratio, 4),
        "put_call_signal": signal,
        "total_volume_24h": round(total_volume, 2),
        "data_source": "deribit",
    }
    set_cache("options", cache_key, result)
    return result
