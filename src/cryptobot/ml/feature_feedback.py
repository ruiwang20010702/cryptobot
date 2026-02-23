"""ML 特征重要性反馈 — 将 top features 注入分析师 prompt"""

import logging

from cryptobot.ml.lgb_scorer import load_latest_model

logger = logging.getLogger(__name__)

# 特征名 → 分析师角色映射
_FEATURE_ROLE_MAP: dict[str, str] = {
    "rsi_14": "technical",
    "macd_hist": "technical",
    "bb_width": "technical",
    "volume_ratio": "technical",
    "atr_pct": "technical",
    "ema_cross": "technical",
    "mtf_trend_score": "technical",
    "mtf_momentum": "technical",
    "funding_rate": "onchain",
    "open_interest_change": "onchain",
    "long_short_ratio": "onchain",
    "taker_buy_ratio": "onchain",
    "fear_greed_value": "sentiment",
    "news_sentiment": "sentiment",
    "btc_correlation": "fundamental",
    "dxy_trend": "sentiment",
    "stablecoin_flow": "sentiment",
    "macro_risk": "sentiment",
    "bid_ask_ratio": "technical",
    "spread_pct": "technical",
}


def build_feature_feedback_addon(top_n: int = 5) -> dict[str, str]:
    """加载最新模型的 feature importance, 按角色分组生成 addon

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
    top_pairs = pairs[:top_n]

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
