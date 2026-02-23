"""ML 模型版本注册表"""

import json
import logging
from dataclasses import asdict, dataclass

from cryptobot.config import DATA_OUTPUT_DIR

logger = logging.getLogger(__name__)

REGISTRY_PATH = DATA_OUTPUT_DIR / "ml" / "registry.json"


@dataclass(frozen=True)
class ModelRecord:
    version: str
    created_at: str
    metrics: dict          # {accuracy, auc_roc, f1, precision, recall}
    training_samples: int
    status: str            # "active" | "rolled_back" | "superseded"


def load_registry() -> list[ModelRecord]:
    """加载模型注册表"""
    if not REGISTRY_PATH.exists():
        return []
    try:
        data = json.loads(REGISTRY_PATH.read_text())
        return [ModelRecord(**r) for r in data]
    except Exception as e:
        logger.warning("注册表加载失败: %s", e)
        return []


def save_registry(records: list[ModelRecord]) -> None:
    """保存模型注册表 (原子写入: .tmp -> rename)"""
    REGISTRY_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = REGISTRY_PATH.with_suffix(".json.tmp")
    tmp.write_text(
        json.dumps([asdict(r) for r in records], indent=2, ensure_ascii=False)
    )
    tmp.rename(REGISTRY_PATH)


def get_active_model() -> ModelRecord | None:
    """获取当前活跃模型记录"""
    records = load_registry()
    for r in reversed(records):
        if r.status == "active":
            return r
    return None
