"""DeFi TVL 趋势数据 (DefiLlama API, 无需 key)

追踪主要公链 TVL 趋势，评估生态健康度。TVL 大幅下降是风险信号。
"""

import logging

import httpx

from cryptobot.cache import get_cache, set_cache

logger = logging.getLogger(__name__)

DEFILLAMA_API = "https://api.llama.fi"
CACHE_TTL = 7200  # 2 小时

# 币种 → 公链映射
_SYMBOL_CHAIN_MAP = {
    "ETHUSDT": "Ethereum",
    "SOLUSDT": "Solana",
    "BNBUSDT": "BSC",
    "AVAXUSDT": "Avalanche",
    "SUIUSDT": "Sui",
}


def _empty_result(symbol: str = "") -> dict:
    return {
        "symbol": symbol,
        "chain": "",
        "current_tvl": 0,
        "tvl_change_1d_pct": 0,
        "tvl_change_7d_pct": 0,
        "tvl_trend": "stable",
        "risk_flag": False,
    }


def get_defi_tvl(symbol: str) -> dict:
    """获取币种关联公链的 TVL 趋势

    Returns:
        {"symbol": str, "chain": str, "current_tvl": float,
         "tvl_change_1d_pct": float, "tvl_change_7d_pct": float,
         "tvl_trend": "growing"|"declining"|"stable",
         "risk_flag": bool}
    """
    chain = _SYMBOL_CHAIN_MAP.get(symbol)
    if not chain:
        return _empty_result(symbol)

    cache_key = f"defi_tvl_{symbol}"
    cached = get_cache("defi_tvl", cache_key, CACHE_TTL)
    if cached:
        return cached

    try:
        resp = httpx.get(
            f"{DEFILLAMA_API}/v2/historicalChainTvl/{chain}",
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        logger.warning("DefiLlama TVL 请求失败 (%s): %s", chain, e)
        return _empty_result(symbol)

    if not data or not isinstance(data, list) or len(data) < 2:
        logger.warning("DefiLlama TVL 数据不足 (%s)", chain)
        return _empty_result(symbol)

    # data 是 [{date: unix_ts, tvl: float}, ...] 按时间正序
    current_tvl = data[-1].get("tvl", 0)
    prev_1d_tvl = data[-2].get("tvl", 0) if len(data) >= 2 else current_tvl
    prev_7d_tvl = data[-8].get("tvl", 0) if len(data) >= 8 else data[0].get("tvl", 0)

    change_1d = (
        (current_tvl - prev_1d_tvl) / prev_1d_tvl * 100 if prev_1d_tvl > 0 else 0
    )
    change_7d = (
        (current_tvl - prev_7d_tvl) / prev_7d_tvl * 100 if prev_7d_tvl > 0 else 0
    )

    # TVL 趋势判断
    if change_7d > 5:
        tvl_trend = "growing"
    elif change_7d < -5:
        tvl_trend = "declining"
    else:
        tvl_trend = "stable"

    result = {
        "symbol": symbol,
        "chain": chain,
        "current_tvl": round(current_tvl, 0),
        "tvl_change_1d_pct": round(change_1d, 3),
        "tvl_change_7d_pct": round(change_7d, 3),
        "tvl_trend": tvl_trend,
        "risk_flag": change_7d < -10,
    }
    set_cache("defi_tvl", cache_key, result)
    return result
