"""ML 模型自动重训"""

import logging
from dataclasses import asdict, dataclass
from datetime import datetime, timezone

from cryptobot.ml.lgb_scorer import (
    ModelMetrics,
    prepare_training_data,
    save_model,
    train_model,
)
from cryptobot.ml.registry import (
    ModelRecord,
    get_active_model,
    load_registry,
    save_registry,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class RetrainResult:
    version: str                       # "v_20260223_120000"
    metrics: dict                      # ModelMetrics as dict
    previous_version: str | None
    previous_metrics: dict | None      # previous ModelMetrics as dict
    action: str                        # "deployed" | "rolled_back" | "first_model" | "skipped"
    reason: str


def _metrics_to_dict(m: ModelMetrics) -> dict:
    return {
        "accuracy": m.accuracy,
        "auc_roc": m.auc_roc,
        "precision": m.precision,
        "recall": m.recall,
        "f1": m.f1,
    }


def run_retrain(
    days: int = 180,
    min_samples: int = 50,
    rollback_ratio: float = 0.03,
) -> RetrainResult:
    """执行一次重训流程

    1. prepare_training_data(days)
    2. train_model(X, y) -> (model, metrics)
    3. 与上一版本对比 AUC (相对比较):
       - 新 AUC >= 旧 AUC * (1 - rollback_ratio) -> 部署新模型
       - 新 AUC < 旧 AUC * (1 - rollback_ratio) -> 回滚到旧模型（3% 退化才回滚）
    4. 更新版本注册表
    """
    # 准备数据
    X, y = prepare_training_data(days=days)
    if len(X) < min_samples:
        return RetrainResult(
            version="",
            metrics={},
            previous_version=None,
            previous_metrics=None,
            action="skipped",
            reason=f"样本不足: {len(X)} < {min_samples}",
        )

    # 训练
    version = datetime.now(tz=timezone.utc).strftime("v_%Y%m%d_%H%M%S")
    model, metrics = train_model(X, y)
    metrics_dict = _metrics_to_dict(metrics)

    # 获取前一版本
    prev = get_active_model()
    prev_version = prev.version if prev else None
    prev_metrics = prev.metrics if prev else None

    if prev is None:
        # 首个模型
        save_model(model, version)
        record = ModelRecord(
            version=version,
            created_at=datetime.now(tz=timezone.utc).isoformat(),
            metrics=metrics_dict,
            training_samples=len(X),
            status="active",
        )
        records = load_registry()
        save_registry([*records, record])

        return RetrainResult(
            version=version,
            metrics=metrics_dict,
            previous_version=None,
            previous_metrics=None,
            action="first_model",
            reason="首个模型，直接部署",
        )

    # 对比 AUC（相对比较: 退化超过 rollback_ratio 才回滚）
    prev_auc = prev.metrics.get("auc_roc", 0)
    new_auc = metrics.auc_roc
    threshold = prev_auc * (1 - rollback_ratio)

    if new_auc >= threshold:
        # 部署新模型
        save_model(model, version)

        # 更新注册表: 旧模型标记 superseded, 新模型标记 active
        records = load_registry()
        updated = []
        for r in records:
            if r.version == prev.version and r.status == "active":
                updated.append(ModelRecord(
                    version=r.version,
                    created_at=r.created_at,
                    metrics=r.metrics,
                    training_samples=r.training_samples,
                    status="superseded",
                ))
            else:
                updated.append(r)

        new_record = ModelRecord(
            version=version,
            created_at=datetime.now(tz=timezone.utc).isoformat(),
            metrics=metrics_dict,
            training_samples=len(X),
            status="active",
        )
        updated.append(new_record)
        save_registry(updated)

        return RetrainResult(
            version=version,
            metrics=metrics_dict,
            previous_version=prev_version,
            previous_metrics=prev_metrics,
            action="deployed",
            reason=(
                f"新模型 AUC {new_auc:.4f} >= "
                f"旧 {prev_auc:.4f} * {1 - rollback_ratio:.2f}"
            ),
        )
    else:
        # 回滚: 记录新模型但标记 rolled_back, 旧模型保持 active
        records = load_registry()
        rolled_back_record = ModelRecord(
            version=version,
            created_at=datetime.now(tz=timezone.utc).isoformat(),
            metrics=metrics_dict,
            training_samples=len(X),
            status="rolled_back",
        )
        save_registry([*records, rolled_back_record])

        return RetrainResult(
            version=version,
            metrics=metrics_dict,
            previous_version=prev_version,
            previous_metrics=prev_metrics,
            action="rolled_back",
            reason=(
                f"新模型 AUC {new_auc:.4f} < "
                f"旧 {prev_auc:.4f} * {1 - rollback_ratio:.2f}, 已回滚"
            ),
        )


def get_model_history() -> list[dict]:
    """获取模型版本历史"""
    records = load_registry()
    return [asdict(r) for r in records]
