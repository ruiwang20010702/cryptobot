"""Node: ml_filter — ML 模型信号过滤

对 trade 节点输出的 decisions 做 ML 二次过滤:
- 无模型 → 全部放行
- ML 方向一致 + 高概率 → 放行
- ML 方向不一致 + 高概率 → 拒绝 (改 no_trade)
- 边界情况 → 降置信度
"""

import logging
from datetime import datetime, timezone

from cryptobot.workflow.state import WorkflowState

logger = logging.getLogger(__name__)

# action → ML direction 映射
_ACTION_TO_DIR = {"long": "up", "short": "down"}


def ml_filter(state: WorkflowState) -> dict:
    """ML 信号过滤节点

    对每个 action != no_trade 的 decision:
    1. 加载最新 ML 模型 (无模型 → 全部放行)
    2. 构建当前特征向量
    3. score_signal() 获取 ML 方向 + 概率
    4. 匹配规则:
       - ML 方向一致 + prob > 0.6 → 通过, 标注 ml_score
       - ML 方向一致 + prob 0.5-0.6 → 通过, confidence -= 10
       - ML 方向不一致 + prob > 0.65 → 拒绝 (改为 no_trade)
       - ML 方向不一致 + prob <= 0.65 → 通过, confidence -= 15
    """
    decisions = state.get("decisions", [])
    if not decisions:
        return {"decisions": []}

    # 尝试加载模型 — 无模型则全部放行
    model = _load_model()
    if model is None:
        return {"decisions": list(decisions)}

    market_data = state.get("market_data", {})
    fear_greed = state.get("fear_greed")
    stablecoin = state.get("stablecoin_flows")
    dxy = state.get("dxy_data")
    macro = state.get("macro_events")

    filtered: list[dict] = []
    for dec in decisions:
        filtered.append(
            _filter_one(dec, model, market_data, fear_greed, stablecoin, dxy, macro)
        )

    passed = sum(1 for d in filtered if d.get("action") != "no_trade")
    total = sum(1 for d in decisions if d.get("action") != "no_trade")
    logger.info("ML 过滤: %d/%d 信号通过", passed, total)

    return {"decisions": filtered}


def _load_model() -> object | None:
    """加载最新 ML 模型，无模型返回 None"""
    try:
        from cryptobot.ml.lgb_scorer import load_latest_model
        model, version = load_latest_model()
        logger.info("ML 过滤使用模型: %s", version)
        return model
    except FileNotFoundError:
        logger.warning("无 ML 模型，全部放行")
        return None
    except Exception as e:
        logger.warning("加载 ML 模型失败: %s，全部放行", e)
        return None


def _filter_one(
    decision: dict,
    model: object,
    market_data: dict,
    fear_greed: dict | None,
    stablecoin: dict | None,
    dxy: dict | None,
    macro: dict | None,
) -> dict:
    """对单个 decision 执行 ML 过滤，返回新 dict"""
    action = decision.get("action", "no_trade")
    if action == "no_trade":
        return dict(decision)

    symbol = decision.get("symbol", "")
    expected_dir = _ACTION_TO_DIR.get(action)
    if expected_dir is None:
        return dict(decision)

    # 构建特征向量
    try:
        score = _score_for_symbol(
            symbol, model, market_data, fear_greed, stablecoin, dxy, macro,
        )
    except Exception as e:
        logger.warning("ML 评分失败 (%s): %s，放行", symbol, e)
        return dict(decision)

    ml_dir = score.direction
    prob = score.probability
    # prob 是 "up" 的概率; 若 direction 是 "down" 则方向概率 = 1 - prob
    dir_prob = prob if ml_dir == "up" else 1.0 - prob
    same_direction = ml_dir == expected_dir

    new_dec = dict(decision)
    new_dec["ml_score"] = {
        "direction": ml_dir,
        "probability": prob,
        "model_version": score.model_version,
    }

    if same_direction and dir_prob > 0.6:
        # 方向一致 + 高概率 → 通过
        return new_dec

    if same_direction:
        # 方向一致 + prob 0.5-0.6 → 降置信度 10
        conf = new_dec.get("confidence", 50)
        new_dec["confidence"] = max(0, conf - 10)
        return new_dec

    # 方向不一致
    if dir_prob > 0.65:
        # 不一致 + ML 高概率 → 拒绝
        new_dec["action"] = "no_trade"
        new_dec["ml_filtered"] = True
        logger.info(
            "ML 拒绝 %s %s (ML=%s prob=%.2f)", symbol, action, ml_dir, prob,
        )
        return new_dec

    # 不一致 + ML 低概率 → 降置信度 15
    conf = new_dec.get("confidence", 50)
    new_dec["confidence"] = max(0, conf - 15)
    return new_dec


def _score_for_symbol(
    symbol: str,
    model: object,
    market_data: dict,
    fear_greed: dict | None,
    stablecoin: dict | None,
    dxy: dict | None,
    macro: dict | None,
) -> object:
    """为单币种构建特征并评分"""
    from cryptobot.features.pipeline import build_feature_vector
    from cryptobot.ml.lgb_scorer import score_signal

    sym_data = market_data.get(symbol, {})
    timestamp = datetime.now(tz=timezone.utc).isoformat()

    fv = build_feature_vector(
        symbol=symbol,
        timestamp=timestamp,
        tech=sym_data.get("tech"),
        multi_tf=sym_data.get("multi_tf"),
        crypto=sym_data.get("crypto"),
        fear_greed=fear_greed,
        news=sym_data.get("coin_news"),
        orderbook=sym_data.get("orderbook"),
        dxy=dxy,
        macro=macro,
        stablecoin=stablecoin,
        btc_corr=sym_data.get("btc_correlation", 0.0),
    )

    return score_signal(symbol=symbol, features=fv.features, model=model)
