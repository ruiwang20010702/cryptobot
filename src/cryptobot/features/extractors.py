"""特征提取函数

从各数据源提取数值特征，供特征管道使用。
每个函数做防御性处理：输入为 None 或空 dict 时返回全零默认值。
"""


def _safe_float(val, default: float = 0.0) -> float:
    """安全转换为 float"""
    if val is None:
        return default
    try:
        f = float(val)
        return f if f == f else default  # NaN check
    except (TypeError, ValueError):
        return default


# ─── 技术指标 ────────────────────────────────────────────────────────────


def extract_tech_features(tech: dict | None) -> dict[str, float]:
    """从技术指标提取特征

    输入: calc_all_indicators() 的输出 (tech_indicators)
    输出: rsi, adx, macd_hist, bb_position, ema_score, atr_pct

    安全提取: 如果 key 缺失返回 0.0
    """
    if not tech:
        return _tech_defaults()

    momentum = tech.get("momentum", {})
    trend = tech.get("trend", {})
    volatility = tech.get("volatility", {})

    rsi = _safe_float(momentum.get("rsi_14"))
    adx = _safe_float(trend.get("adx"))

    # MACD 柱状值标准化: 除以 close 价格 * 100
    macd_hist_raw = _safe_float(trend.get("macd_hist"))
    close = _safe_float(tech.get("latest_close"), default=1.0)
    macd_hist = (macd_hist_raw / close * 100) if close > 0 else 0.0

    bb_position = _safe_float(volatility.get("bb_position"))

    # EMA 排列得分: bullish=1, bearish=-1, mixed=0, unknown=0
    ema_align = trend.get("ema_alignment", "unknown")
    ema_score_map = {"bullish": 1.0, "bearish": -1.0, "mixed": 0.0}
    ema_score = ema_score_map.get(ema_align, 0.0)

    atr_pct = _safe_float(volatility.get("atr_pct"))

    return {
        "rsi": rsi,
        "adx": adx,
        "macd_hist": round(macd_hist, 6),
        "bb_position": bb_position,
        "ema_score": ema_score,
        "atr_pct": atr_pct,
    }


def _tech_defaults() -> dict[str, float]:
    return {
        "rsi": 0.0,
        "adx": 0.0,
        "macd_hist": 0.0,
        "bb_position": 0.0,
        "ema_score": 0.0,
        "atr_pct": 0.0,
    }


# ─── 多时间框架 ──────────────────────────────────────────────────────────


def extract_multi_tf_features(multi_tf: dict | None) -> dict[str, float]:
    """从多时间框架分析提取特征

    输入: calc_multi_timeframe() 的输出
    输出: tf_alignment_score, tf_bullish_count, tf_bearish_count
    """
    if not multi_tf:
        return _multi_tf_defaults()

    timeframes = multi_tf.get("timeframes", {})
    if not timeframes:
        return _multi_tf_defaults()

    bullish_count = 0.0
    bearish_count = 0.0
    for _tf_key, tf_data in timeframes.items():
        direction = tf_data.get("direction", "neutral")
        if direction == "bullish":
            bullish_count += 1.0
        elif direction == "bearish":
            bearish_count += 1.0

    # alignment_score: -1(全空) 到 1(全多)
    total = len(timeframes) if timeframes else 1.0
    alignment_score = (bullish_count - bearish_count) / total

    return {
        "tf_alignment_score": round(alignment_score, 4),
        "tf_bullish_count": bullish_count,
        "tf_bearish_count": bearish_count,
    }


def _multi_tf_defaults() -> dict[str, float]:
    return {
        "tf_alignment_score": 0.0,
        "tf_bullish_count": 0.0,
        "tf_bearish_count": 0.0,
    }


# ─── 链上数据 ────────────────────────────────────────────────────────────


def extract_onchain_features(crypto: dict | None) -> dict[str, float]:
    """从链上数据提取特征

    输入: collect 节点中的 crypto_data (含 funding, open_interest, long_short 等)
    输出: funding_rate, oi_change_pct, long_short_ratio
    """
    if not crypto:
        return _onchain_defaults()

    # funding_rate
    funding = crypto.get("funding", {})
    funding_rate = _safe_float(funding.get("current_rate"))

    # OI 变化
    oi = crypto.get("open_interest", {})
    oi_change_pct = _safe_float(oi.get("change_pct"))

    # 多空比
    ls = crypto.get("long_short", {})
    long_short_ratio = _safe_float(ls.get("current_ratio"), default=1.0)

    return {
        "funding_rate": funding_rate,
        "oi_change_pct": oi_change_pct,
        "long_short_ratio": long_short_ratio,
    }


def _onchain_defaults() -> dict[str, float]:
    return {
        "funding_rate": 0.0,
        "oi_change_pct": 0.0,
        "long_short_ratio": 1.0,
    }


# ─── 情绪数据 ────────────────────────────────────────────────────────────


def extract_sentiment_features(
    fear_greed: dict | None,
    news: dict | None,
) -> dict[str, float]:
    """从情绪数据提取特征

    输入: fear_greed_data, news_data
    输出: fear_greed_index, news_sentiment
    """
    fg_index = 0.0
    if fear_greed:
        fg_index = _safe_float(fear_greed.get("current_value"), default=50.0)

    news_sentiment = 0.0
    if news:
        # news_data 可能有 sentiment 字段或需要从 articles 聚合
        news_sentiment = _safe_float(news.get("sentiment_score"))

    return {
        "fear_greed_index": fg_index,
        "news_sentiment": news_sentiment,
    }


# ─── 订单簿 ──────────────────────────────────────────────────────────────


def extract_orderbook_features(orderbook: dict | None) -> dict[str, float]:
    """从订单簿提取特征

    输入: orderbook_data (get_orderbook_depth 输出)
    输出: bid_ask_ratio, spread_pct
    """
    if not orderbook:
        return _orderbook_defaults()

    bid_ask_ratio = _safe_float(orderbook.get("bid_ask_ratio"), default=1.0)
    spread_pct = _safe_float(orderbook.get("spread_pct"))

    return {
        "bid_ask_ratio": bid_ask_ratio,
        "spread_pct": spread_pct,
    }


def _orderbook_defaults() -> dict[str, float]:
    return {
        "bid_ask_ratio": 1.0,
        "spread_pct": 0.0,
    }


# ─── 宏观数据 ────────────────────────────────────────────────────────────


def extract_macro_features(
    dxy: dict | None,
    macro: dict | None,
    stablecoin: dict | None,
) -> dict[str, float]:
    """从宏观数据提取特征

    输入: dxy_data, macro_data, stablecoin_data
    输出: dxy_value, high_impact_events, stablecoin_flow
    """
    dxy_value = 0.0
    if dxy:
        dxy_value = _safe_float(dxy.get("current_value"))

    high_impact_events = 0.0
    if macro:
        events = macro.get("events", [])
        high_impact_events = float(
            sum(1 for e in events if e.get("impact") == "high")
        )

    stablecoin_flow = 0.0
    if stablecoin:
        stablecoin_flow = _safe_float(stablecoin.get("net_flow_7d"))

    return {
        "dxy_value": dxy_value,
        "high_impact_events": high_impact_events,
        "stablecoin_flow": stablecoin_flow,
    }


# ─── BTC 相关性 ──────────────────────────────────────────────────────────


def extract_correlation_features(btc_corr: float = 0.0) -> dict[str, float]:
    """从 BTC 相关性提取特征

    输入: 与 BTC 的相关系数 -1 到 1
    输出: btc_correlation, btc_corr_category
    """
    corr = _safe_float(btc_corr)
    abs_corr = abs(corr)

    # 编码: high=0.8, medium=0.5, low=0.2
    if abs_corr >= 0.7:
        category = 0.8
    elif abs_corr >= 0.4:
        category = 0.5
    else:
        category = 0.2

    return {
        "btc_correlation": corr,
        "btc_corr_category": category,
    }
