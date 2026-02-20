"""加密货币特有指标

- 资金费率趋势分析
- OI 变化率
- 多空比偏离度
- 综合评分
"""

from cryptobot.data.onchain import get_funding_rate, get_open_interest_hist, get_taker_buy_sell_ratio
from cryptobot.data.sentiment import get_long_short_ratio, get_top_trader_long_short


def calc_crypto_indicators(symbol: str = "BTCUSDT") -> dict:
    """计算加密货币特有指标"""
    funding = get_funding_rate(symbol)
    oi = get_open_interest_hist(symbol, period="1h", limit=48)
    taker = get_taker_buy_sell_ratio(symbol, period="1h", limit=48)
    ls_ratio = get_long_short_ratio(symbol, period="1h", limit=30)
    top_ls = get_top_trader_long_short(symbol, period="1h", limit=30)

    # 资金费率分析
    funding_analysis = _analyze_funding(funding)

    # OI 分析
    oi_analysis = _analyze_oi(oi)

    # 主动买卖比分析
    taker_analysis = _analyze_taker(taker)

    # 多空比分析
    ls_analysis = _analyze_long_short(ls_ratio, top_ls)

    # 综合评分
    score = _composite_score(funding_analysis, oi_analysis, taker_analysis, ls_analysis)

    return {
        "symbol": symbol,
        "funding": funding_analysis,
        "open_interest": oi_analysis,
        "taker_ratio": taker_analysis,
        "long_short": ls_analysis,
        "composite": score,
    }


def _analyze_funding(data: dict) -> dict:
    """资金费率分析"""
    rate = data.get("current_rate", 0)
    avg = data.get("avg_rate_30", 0)
    pos_count = data.get("positive_count", 0)
    neg_count = data.get("negative_count", 0)

    # 资金费率信号
    if rate > 0.001:
        signal = "极度看多 (费率高，做空可能获利)"
        bias = "short"
        score = -2
    elif rate > 0.0005:
        signal = "偏多 (费率偏高)"
        bias = "short_lean"
        score = -1
    elif rate < -0.001:
        signal = "极度看空 (费率负，做多可能获利)"
        bias = "long"
        score = 2
    elif rate < -0.0005:
        signal = "偏空 (费率偏低)"
        bias = "long_lean"
        score = 1
    else:
        signal = "中性"
        bias = "neutral"
        score = 0

    return {
        "current_rate": rate,
        "current_rate_pct": round(rate * 100, 4),
        "avg_rate_pct": round(avg * 100, 4),
        "positive_ratio": pos_count / max(pos_count + neg_count, 1),
        "signal": signal,
        "bias": bias,
        "score": score,
    }


def _analyze_oi(data: dict) -> dict:
    """持仓量分析"""
    change_pct = data.get("oi_change_pct", 0)
    current = data.get("current_oi_value", 0)

    if change_pct > 10:
        signal = "OI 大幅上升 (新资金入场)"
        trend = "increasing"
    elif change_pct > 3:
        signal = "OI 上升"
        trend = "increasing"
    elif change_pct < -10:
        signal = "OI 大幅下降 (资金撤离)"
        trend = "decreasing"
    elif change_pct < -3:
        signal = "OI 下降"
        trend = "decreasing"
    else:
        signal = "OI 平稳"
        trend = "stable"

    return {
        "current_oi_value": current,
        "change_pct": round(change_pct, 2),
        "trend": trend,
        "signal": signal,
    }


def _analyze_taker(data: dict) -> dict:
    """主动买卖比分析"""
    ratio = data.get("current_ratio", 1.0)
    avg = data.get("avg_ratio", 1.0)
    bullish = data.get("bullish_count", 0)
    bearish = data.get("bearish_count", 0)

    if ratio > 1.2:
        signal = "主动买入强势"
        bias = "bullish"
        score = 1.5
    elif ratio > 1.05:
        signal = "主动买入偏多"
        bias = "bullish_lean"
        score = 0.5
    elif ratio < 0.8:
        signal = "主动卖出强势"
        bias = "bearish"
        score = -1.5
    elif ratio < 0.95:
        signal = "主动卖出偏多"
        bias = "bearish_lean"
        score = -0.5
    else:
        signal = "买卖均衡"
        bias = "neutral"
        score = 0

    return {
        "current_ratio": round(ratio, 4),
        "avg_ratio": round(avg, 4),
        "bullish_periods": bullish,
        "bearish_periods": bearish,
        "signal": signal,
        "bias": bias,
        "score": score,
    }


def _analyze_long_short(ls_data: dict, top_data: dict) -> dict:
    """多空比分析"""
    global_ratio = ls_data.get("current_ratio", 1.0)
    global_long = ls_data.get("current_long_pct", 50)
    global_short = ls_data.get("current_short_pct", 50)

    top_ratio = top_data.get("current_ratio", 1.0)
    top_long = top_data.get("current_long_pct", 50)

    # 大户与散户分歧
    divergence = top_ratio - global_ratio
    if abs(divergence) > 0.3:
        divergence_signal = "大户与散户严重分歧"
    elif abs(divergence) > 0.1:
        divergence_signal = "大户与散户存在分歧"
    else:
        divergence_signal = "大户散户方向一致"

    # 信号: 跟随大户
    if top_ratio > 1.2:
        bias = "bullish"
        score = 1
    elif top_ratio < 0.8:
        bias = "bearish"
        score = -1
    else:
        bias = "neutral"
        score = 0

    return {
        "global_long_pct": round(global_long, 1),
        "global_short_pct": round(global_short, 1),
        "global_ratio": round(global_ratio, 4),
        "top_trader_ratio": round(top_ratio, 4),
        "top_trader_long_pct": round(top_long, 1),
        "divergence": round(divergence, 4),
        "divergence_signal": divergence_signal,
        "bias": bias,
        "score": score,
    }


def _composite_score(funding: dict, oi: dict, taker: dict, ls: dict) -> dict:
    """综合评分"""
    score = 0
    signals = []

    # 资金费率权重 (反向指标)
    score += funding.get("score", 0) * 1.5
    if funding["score"] != 0:
        signals.append(funding["signal"])

    # 主动买卖比
    score += taker.get("score", 0) * 1.0
    if taker["score"] != 0:
        signals.append(taker["signal"])

    # 多空比 (跟大户)
    score += ls.get("score", 0) * 1.0
    if ls["score"] != 0:
        signals.append(f"大户偏{ls['bias']}")

    # OI 变化 (辅助)
    if oi["trend"] == "increasing":
        signals.append("资金入场")
    elif oi["trend"] == "decreasing":
        signals.append("资金撤离")

    score = max(-10, min(10, score))
    if score > 1.5:
        bias = "bullish"
    elif score < -1.5:
        bias = "bearish"
    else:
        bias = "neutral"

    return {
        "score": round(score, 1),
        "bias": bias,
        "signals": signals,
    }
