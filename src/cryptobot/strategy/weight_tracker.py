"""策略权重管理 -- 根据 regime 动态分配策略权重"""

import json
import logging
from dataclasses import asdict, dataclass
from datetime import datetime, timezone

from cryptobot.config import DATA_OUTPUT_DIR

logger = logging.getLogger(__name__)

WEIGHTS_PATH = DATA_OUTPUT_DIR / "evolution" / "strategy_weights.json"


@dataclass(frozen=True)
class StrategyWeight:
    strategy: str  # "ai_trend" | "mean_reversion" | "grid" | "funding_arb"
    weight: float  # 0.0 - 1.0, 所有策略权重之和 = 1.0
    reason: str


@dataclass(frozen=True)
class WeightAllocation:
    regime: str
    weights: list[StrategyWeight]
    updated_at: str


# 默认权重表
_DEFAULT_WEIGHTS: dict[str, list[StrategyWeight]] = {
    "trending": [
        StrategyWeight("ai_trend", 0.8, "趋势市主力"),
        StrategyWeight("mean_reversion", 0.0, "趋势市不用均值回归"),
        StrategyWeight("grid", 0.2, "网格辅助"),
    ],
    "ranging": [
        StrategyWeight("ai_trend", 0.2, "震荡市降低 AI 趋势权重"),
        StrategyWeight("mean_reversion", 0.5, "震荡市主力"),
        StrategyWeight("grid", 0.3, "网格辅助"),
    ],
    "volatile": [
        StrategyWeight("ai_trend", 0.0, "高波动不交易"),
        StrategyWeight("mean_reversion", 0.0, "高波动不交易"),
        StrategyWeight("grid", 0.0, "高波动不交易"),
    ],
}


def get_weights(regime: str) -> WeightAllocation:
    """获取当前 regime 下的策略权重

    优先从持久化文件加载（如果 regime 匹配），否则用默认值。
    """
    saved = load_weights()
    if saved is not None and saved.regime == regime:
        return saved

    weights = _DEFAULT_WEIGHTS.get(regime, _DEFAULT_WEIGHTS["trending"])
    return WeightAllocation(
        regime=regime,
        weights=list(weights),
        updated_at=datetime.now(tz=timezone.utc).isoformat(),
    )


def save_weights(allocation: WeightAllocation) -> None:
    """持久化权重 (原子写入)"""
    WEIGHTS_PATH.parent.mkdir(parents=True, exist_ok=True)
    data = {
        "regime": allocation.regime,
        "weights": [asdict(w) for w in allocation.weights],
        "updated_at": allocation.updated_at,
    }
    tmp = WEIGHTS_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False))
    tmp.rename(WEIGHTS_PATH)


def load_weights() -> WeightAllocation | None:
    """加载最新权重"""
    if not WEIGHTS_PATH.exists():
        return None
    try:
        data = json.loads(WEIGHTS_PATH.read_text())
        weights = [StrategyWeight(**w) for w in data["weights"]]
        return WeightAllocation(
            regime=data["regime"],
            weights=weights,
            updated_at=data["updated_at"],
        )
    except Exception as e:
        logger.warning("权重加载失败: %s", e)
        return None
