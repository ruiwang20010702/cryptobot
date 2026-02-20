"""强平/清算数据 (Binance 公开端点，无需 key)"""

import numpy as np
import httpx

from cryptobot.cache import get_cache, set_cache

BINANCE_FAPI = "https://fapi.binance.com"
CACHE_TTL = 900  # 15 分钟


def get_force_orders(symbol: str = "BTCUSDT") -> dict:
    """获取最近强平记录，统计多/空清算及聚集区域"""
    cache_key = f"force_orders_{symbol}"
    cached = get_cache("liquidation", cache_key, CACHE_TTL)
    if cached:
        return cached

    # Binance forceOrders 端点: 最近 7 天的强平记录
    try:
        resp = httpx.get(
            f"{BINANCE_FAPI}/fapi/v1/forceOrders",
            params={"symbol": symbol, "limit": 100},
            timeout=10,
        )
        resp.raise_for_status()
        raw = resp.json()
    except Exception:
        # 某些币种可能没有强平数据
        result = _empty_result(symbol)
        set_cache("liquidation", cache_key, result)
        return result

    if not raw:
        result = _empty_result(symbol)
        set_cache("liquidation", cache_key, result)
        return result

    long_liquidations = []
    short_liquidations = []

    for order in raw:
        side = order.get("side", "")
        price = float(order.get("price", 0))
        qty = float(order.get("origQty", 0))
        amount = price * qty

        record = {"price": price, "qty": qty, "amount": amount, "time": order.get("time", 0)}

        # side=SELL 表示多头被强平 (系统卖出), side=BUY 表示空头被强平 (系统买入)
        if side == "SELL":
            long_liquidations.append(record)
        elif side == "BUY":
            short_liquidations.append(record)

    long_total = sum(r["amount"] for r in long_liquidations)
    short_total = sum(r["amount"] for r in short_liquidations)

    # 清算聚集区域 (以 ATR 为桶宽分桶)
    all_prices = [r["price"] for r in long_liquidations + short_liquidations]
    clusters = _calc_clusters(all_prices) if all_prices else []

    # 清算强度
    total_count = len(long_liquidations) + len(short_liquidations)
    if total_count > 50:
        intensity = "extreme"
    elif total_count > 20:
        intensity = "high"
    elif total_count > 5:
        intensity = "moderate"
    else:
        intensity = "low"

    result = {
        "symbol": symbol,
        "long_liq_count": len(long_liquidations),
        "short_liq_count": len(short_liquidations),
        "long_liq_amount": round(long_total, 2),
        "short_liq_amount": round(short_total, 2),
        "net_liq_bias": "long_squeezed" if long_total > short_total * 1.5 else (
            "short_squeezed" if short_total > long_total * 1.5 else "balanced"
        ),
        "intensity": intensity,
        "clusters": clusters,
        "total_count": total_count,
    }
    set_cache("liquidation", cache_key, result)
    return result


def _empty_result(symbol: str) -> dict:
    return {
        "symbol": symbol,
        "long_liq_count": 0,
        "short_liq_count": 0,
        "long_liq_amount": 0,
        "short_liq_amount": 0,
        "net_liq_bias": "no_data",
        "intensity": "low",
        "clusters": [],
        "total_count": 0,
    }


def _calc_clusters(prices: list[float], n_bins: int = 5) -> list[dict]:
    """将清算价格按区间分桶，找出聚集区"""
    if len(prices) < 2:
        return []

    arr = np.array(prices)
    lo, hi = float(np.min(arr)), float(np.max(arr))
    if hi == lo:
        return [{"range_low": lo, "range_high": hi, "count": len(prices)}]

    bin_width = (hi - lo) / n_bins
    clusters = []
    for i in range(n_bins):
        low = lo + i * bin_width
        high = low + bin_width
        count = int(np.sum((arr >= low) & (arr < high + (1 if i == n_bins - 1 else 0))))
        if count > 0:
            clusters.append({
                "range_low": round(low, 2),
                "range_high": round(high, 2),
                "count": count,
            })

    clusters.sort(key=lambda x: x["count"], reverse=True)
    return clusters
