"""特征存储

持久化/加载特征矩阵到 data/output/features/{date}.json。
自动清理旧文件（默认保留 90 天）。
"""

import json
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path

from cryptobot.config import DATA_OUTPUT_DIR
from cryptobot.features.pipeline import FeatureMatrix, FeatureVector

logger = logging.getLogger(__name__)

FEATURES_DIR = DATA_OUTPUT_DIR / "features"


def save_features(matrix: FeatureMatrix) -> Path:
    """持久化到 data/output/features/{date}.json

    保留 90 天数据，自动清理旧文件。
    """
    FEATURES_DIR.mkdir(parents=True, exist_ok=True)

    date_str = datetime.now(tz=timezone.utc).strftime("%Y-%m-%d")
    path = FEATURES_DIR / f"{date_str}.json"

    data = {
        "date": date_str,
        "feature_names": matrix.feature_names,
        "vectors": [
            {
                "symbol": v.symbol,
                "timestamp": v.timestamp,
                "features": v.features,
            }
            for v in matrix.vectors
        ],
    }

    tmp_path = path.with_suffix(".json.tmp")
    tmp_path.write_text(json.dumps(data, ensure_ascii=False, indent=2))
    tmp_path.rename(path)

    logger.info("特征矩阵已保存: %s (%d 向量)", path.name, len(matrix.vectors))

    # 自动清理
    cleanup_old_features()

    return path


def load_latest_features() -> FeatureMatrix | None:
    """加载最新的特征矩阵"""
    if not FEATURES_DIR.exists():
        return None

    files = sorted(FEATURES_DIR.glob("*.json"), reverse=True)
    if not files:
        return None

    return _load_from_file(files[0])


def _load_from_file(path: Path) -> FeatureMatrix | None:
    """从文件加载特征矩阵"""
    try:
        data = json.loads(path.read_text())
    except (json.JSONDecodeError, OSError) as e:
        logger.warning("特征文件加载失败 %s: %s", path, e)
        return None

    feature_names = data.get("feature_names", [])
    raw_vectors = data.get("vectors", [])

    vectors = [
        FeatureVector(
            symbol=v["symbol"],
            timestamp=v["timestamp"],
            features=v["features"],
        )
        for v in raw_vectors
    ]

    return FeatureMatrix(vectors=vectors, feature_names=feature_names)


def cleanup_old_features(keep_days: int = 90) -> int:
    """清理旧特征文件，返回删除的文件数"""
    if not FEATURES_DIR.exists():
        return 0

    cutoff = datetime.now(tz=timezone.utc) - timedelta(days=keep_days)
    cutoff_str = cutoff.strftime("%Y-%m-%d")
    deleted = 0

    for f in FEATURES_DIR.glob("*.json"):
        # 文件名格式: YYYY-MM-DD.json
        stem = f.stem
        if stem < cutoff_str:
            f.unlink()
            deleted += 1
            logger.debug("清理旧特征文件: %s", f.name)

    return deleted
