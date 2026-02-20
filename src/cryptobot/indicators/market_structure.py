"""市场结构分析 — BTC 联动性

纯本地计算，零 API 调用。
"""

import numpy as np

from cryptobot.indicators.calculator import load_klines


def calc_btc_correlation(symbol: str, btc_tech: dict | None = None, market_overview: dict | None = None) -> dict:
    """计算 symbol 与 BTC 的 Pearson 相关系数及联动含义"""
    if symbol == "BTCUSDT":
        return {
            "symbol": symbol,
            "correlation": 1.0,
            "correlation_class": "self",
            "btc_trend": btc_tech.get("signals", {}).get("bias", "neutral") if btc_tech else "unknown",
            "btc_rsi": btc_tech.get("momentum", {}).get("rsi_14") if btc_tech else None,
            "btc_dominance": (market_overview or {}).get("btc_dominance"),
            "implication": "BTC 自身",
        }

    # 读取 4h K 线
    try:
        df_btc = load_klines("BTCUSDT", "4h")
        df_sym = load_klines(symbol, "4h")
    except FileNotFoundError:
        return {"symbol": symbol, "correlation": None, "error": "缺少 K 线数据"}

    # 对齐时间取交集，计算收益率
    btc_close = df_btc["close"].astype(np.float64)
    sym_close = df_sym["close"].astype(np.float64)

    common = btc_close.index.intersection(sym_close.index)
    if len(common) < 30:
        return {"symbol": symbol, "correlation": None, "error": "重叠数据不足 30 条"}

    common = common.sort_values()[-30:]  # 最近 30 根
    btc_ret = btc_close.loc[common].pct_change().dropna().values
    sym_ret = sym_close.loc[common].pct_change().dropna().values

    min_len = min(len(btc_ret), len(sym_ret))
    if min_len < 10:
        return {"symbol": symbol, "correlation": None, "error": "有效收益率不足"}

    corr = float(np.corrcoef(btc_ret[:min_len], sym_ret[:min_len])[0, 1])
    if np.isnan(corr):
        corr = 0.0

    # 分类
    abs_corr = abs(corr)
    if abs_corr > 0.7:
        corr_class = "high"
    elif abs_corr > 0.4:
        corr_class = "medium"
    else:
        corr_class = "low"

    # BTC 信息
    btc_bias = btc_tech.get("signals", {}).get("bias", "neutral") if btc_tech else "unknown"
    btc_rsi = btc_tech.get("momentum", {}).get("rsi_14") if btc_tech else None
    btc_dom = (market_overview or {}).get("btc_dominance")

    # 联动含义
    if corr_class == "high" and corr > 0:
        implication = f"与 BTC 高度正相关 ({corr:.2f})，BTC {btc_bias} 时预计同向运动"
    elif corr_class == "high" and corr < 0:
        implication = f"与 BTC 高度负相关 ({corr:.2f})，可作对冲"
    elif corr_class == "medium":
        implication = f"与 BTC 中等相关 ({corr:.2f})，需关注但有独立行情空间"
    else:
        implication = f"与 BTC 弱相关 ({corr:.2f})，走势相对独立"

    return {
        "symbol": symbol,
        "correlation": round(corr, 4),
        "correlation_class": corr_class,
        "btc_trend": btc_bias,
        "btc_rsi": btc_rsi,
        "btc_dominance": btc_dom,
        "implication": implication,
    }
