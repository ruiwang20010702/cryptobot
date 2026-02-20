"""稳定币流入流出数据 (DefiLlama 免费 API, 无需 key)

获取 USDT/USDC 铸造销毁数据，判断资金入场/离场。
"""

import logging

import httpx

from cryptobot.cache import get_cache, set_cache

logger = logging.getLogger(__name__)

DEFILLAMA_API = "https://stablecoins.llama.fi"
CACHE_TTL = 3600  # 1 小时 (稳定币市值日度变化)

# 关注的主要稳定币名称 (DefiLlama 返回的 name 字段)
_TARGET_STABLES = {"Tether", "USD Coin"}


def _empty_result() -> dict:
    return {
        "total_mcap": 0,
        "change_1d_pct": 0,
        "change_7d_pct": 0,
        "flow_signal": "neutral",
        "breakdown": {},
    }


def get_stablecoin_flows() -> dict:
    """获取 USDT+USDC 流入流出信号

    Returns:
        {"total_mcap": float, "change_1d_pct": float, "change_7d_pct": float,
         "flow_signal": "inflow"|"outflow"|"neutral",
         "breakdown": {name: {mcap, change_1d_pct, change_7d_pct}}}
    """
    cache_key = "stablecoin_flows"
    cached = get_cache("stablecoin", cache_key, CACHE_TTL)
    if cached:
        return cached

    try:
        resp = httpx.get(
            f"{DEFILLAMA_API}/stablecoins",
            params={"includePrices": "true"},
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        logger.warning("DefiLlama API 请求失败: %s", e)
        return _empty_result()

    peggedAssets = data.get("peggedAssets", [])
    if not peggedAssets:
        logger.warning("DefiLlama 返回空数据")
        return _empty_result()

    total_mcap = 0.0
    total_prev_day = 0.0
    total_prev_week = 0.0
    breakdown = {}

    for asset in peggedAssets:
        name = asset.get("name", "")
        if name not in _TARGET_STABLES:
            continue

        # circulating 是各链合计
        circ = asset.get("circulating", {})
        mcap = circ.get("peggedUSD", 0) or 0

        prev_day = (asset.get("circulatingPrevDay", {}) or {}).get("peggedUSD", 0) or 0
        prev_week = (asset.get("circulatingPrevWeek", {}) or {}).get("peggedUSD", 0) or 0

        total_mcap += mcap
        total_prev_day += prev_day
        total_prev_week += prev_week

        change_1d = (mcap - prev_day) / prev_day * 100 if prev_day > 0 else 0
        change_7d = (mcap - prev_week) / prev_week * 100 if prev_week > 0 else 0

        breakdown[name] = {
            "mcap": round(mcap, 0),
            "change_1d_pct": round(change_1d, 3),
            "change_7d_pct": round(change_7d, 3),
        }

    change_1d_pct = (
        (total_mcap - total_prev_day) / total_prev_day * 100
        if total_prev_day > 0 else 0
    )
    change_7d_pct = (
        (total_mcap - total_prev_week) / total_prev_week * 100
        if total_prev_week > 0 else 0
    )

    # 判断流入/流出信号
    if change_1d_pct > 0.5:
        flow_signal = "inflow"
    elif change_1d_pct < -0.5:
        flow_signal = "outflow"
    else:
        flow_signal = "neutral"

    result = {
        "total_mcap": round(total_mcap, 0),
        "change_1d_pct": round(change_1d_pct, 3),
        "change_7d_pct": round(change_7d_pct, 3),
        "flow_signal": flow_signal,
        "breakdown": breakdown,
    }
    set_cache("stablecoin", cache_key, result)
    return result
