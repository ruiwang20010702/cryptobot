"""ML 特征重要性反馈 — 将 top features 注入分析师 prompt"""

import logging
import random

from cryptobot.ml.lgb_scorer import load_latest_model

logger = logging.getLogger(__name__)

# 特征名 → 分析师角色映射（与 extractors.py 实际输出对齐）
_FEATURE_ROLE_MAP: dict[str, str] = {
    # extract_tech_features
    "rsi": "technical",
    "adx": "technical",
    "macd_hist": "technical",
    "bb_position": "technical",
    "ema_score": "technical",
    "atr_pct": "technical",
    # extract_multi_tf_features
    "tf_alignment_score": "technical",
    "tf_bullish_count": "technical",
    "tf_bearish_count": "technical",
    # extract_onchain_features
    "funding_rate": "onchain",
    "oi_change_pct": "onchain",
    "long_short_ratio": "onchain",
    # extract_sentiment_features
    "fear_greed_index": "sentiment",
    "news_sentiment": "sentiment",
    # extract_orderbook_features
    "bid_ask_ratio": "technical",
    "spread_pct": "technical",
    # extract_macro_features
    "dxy_value": "sentiment",
    "high_impact_events": "sentiment",
    "stablecoin_flow": "sentiment",
    # extract_correlation_features
    "btc_correlation": "fundamental",
    "btc_corr_category": "fundamental",
}


def build_feature_feedback_addon(
    top_n: int = 5,
    epsilon: float = 0.1,
) -> dict[str, str]:
    """加载最新模型的 feature importance, 按角色分组生成 addon

    epsilon-greedy 探索: 以 epsilon 概率随机替换 1 个 top 特征为非 top 特征，
    防止特征反馈陷入局部最优。

    Returns: {"technical": "\\n### ML ...", "onchain": "...", ...}
    无模型时返回空 dict。
    """
    try:
        booster, _version = load_latest_model()
    except FileNotFoundError:
        logger.debug("无可用 ML 模型，跳过特征反馈")
        return {}

    names = booster.feature_name()
    importances = booster.feature_importance(importance_type="gain")

    # 按重要性降序排列，取 top_n
    pairs = sorted(zip(names, importances), key=lambda x: -x[1])
    top_pairs = list(pairs[:top_n])

    # epsilon-greedy 探索: 随机替换 1 个 top 特征为非 top 特征
    rest_pairs = pairs[top_n:]
    if rest_pairs and top_pairs and random.random() < epsilon:
        replace_idx = random.randrange(len(top_pairs))
        explore_feat = random.choice(rest_pairs)
        logger.info(
            "epsilon-greedy 探索: 替换 %s → %s",
            top_pairs[replace_idx][0], explore_feat[0],
        )
        top_pairs[replace_idx] = explore_feat

    # 按角色分组
    grouped: dict[str, list[tuple[str, float]]] = {}
    for name, imp in top_pairs:
        role = _FEATURE_ROLE_MAP.get(name, "technical")
        grouped.setdefault(role, []).append((name, imp))

    # 为每个角色生成 addon
    return {role: _format_addon(role, feats) for role, feats in grouped.items()}


def _format_addon(role: str, features: list[tuple[str, float]]) -> str:
    """格式化单个角色的 addon 文本"""
    lines = [
        "\n### ML 特征重要性提示",
        "以下特征在 ML 模型中对预测最重要，分析时请重点关注:",
    ]
    for name, imp in features:
        lines.append(f"- {name} (重要性: {imp:.1f})")
    return "\n".join(lines)
