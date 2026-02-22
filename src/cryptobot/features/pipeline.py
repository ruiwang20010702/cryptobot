"""特征管道

构建特征向量/矩阵，标准化，导出。纯 Python 实现（无 numpy）。
"""

import math
from dataclasses import dataclass

from cryptobot.features.extractors import (
    extract_correlation_features,
    extract_macro_features,
    extract_multi_tf_features,
    extract_onchain_features,
    extract_orderbook_features,
    extract_sentiment_features,
    extract_tech_features,
)


@dataclass(frozen=True)
class FeatureVector:
    """单币种单时间点的特征向量"""

    symbol: str
    timestamp: str  # ISO format
    features: dict[str, float]  # ~20-30 维特征


@dataclass(frozen=True)
class FeatureMatrix:
    """多币种/多时间点的特征矩阵"""

    vectors: list[FeatureVector]
    feature_names: list[str]


def build_feature_vector(
    symbol: str,
    timestamp: str,
    tech: dict | None = None,
    multi_tf: dict | None = None,
    crypto: dict | None = None,
    fear_greed: dict | None = None,
    news: dict | None = None,
    orderbook: dict | None = None,
    dxy: dict | None = None,
    macro: dict | None = None,
    stablecoin: dict | None = None,
    btc_corr: float = 0.0,
) -> FeatureVector:
    """构建单币种单时间点的特征向量

    调用所有 extractor，合并为一个 flat dict。
    """
    features: dict[str, float] = {}

    features.update(extract_tech_features(tech))
    features.update(extract_multi_tf_features(multi_tf))
    features.update(extract_onchain_features(crypto))
    features.update(extract_sentiment_features(fear_greed, news))
    features.update(extract_orderbook_features(orderbook))
    features.update(extract_macro_features(dxy, macro, stablecoin))
    features.update(extract_correlation_features(btc_corr))

    return FeatureVector(symbol=symbol, timestamp=timestamp, features=features)


def build_feature_matrix(
    vectors: list[FeatureVector],
) -> FeatureMatrix:
    """从多个向量构建矩阵

    feature_names = 所有向量 features key 的并集（有序）。
    """
    all_names: set[str] = set()
    for vec in vectors:
        all_names.update(vec.features.keys())

    feature_names = sorted(all_names)
    return FeatureMatrix(vectors=vectors, feature_names=feature_names)


def normalize_features(
    matrix: FeatureMatrix,
    method: str = "z_score",
) -> FeatureMatrix:
    """标准化特征矩阵 (纯 Python)

    z_score: (x - mean) / std 对每个 feature 列
    min_max: (x - min) / (max - min)

    对于 std=0 或 max==min 的列，标准化值设为 0.0。
    """
    if not matrix.vectors:
        return matrix

    n = len(matrix.vectors)
    names = matrix.feature_names

    if method == "z_score":
        new_vectors = _normalize_z_score(matrix.vectors, names, n)
    elif method == "min_max":
        new_vectors = _normalize_min_max(matrix.vectors, names, n)
    else:
        msg = f"未知标准化方法: {method}"
        raise ValueError(msg)

    return FeatureMatrix(vectors=new_vectors, feature_names=names)


def _normalize_z_score(
    vectors: list[FeatureVector],
    names: list[str],
    n: int,
) -> list[FeatureVector]:
    """z-score 标准化"""
    # 计算每个特征的 mean 和 std
    stats: dict[str, tuple[float, float]] = {}
    for name in names:
        values = [v.features.get(name, 0.0) for v in vectors]
        mean = sum(values) / n
        variance = sum((x - mean) ** 2 for x in values) / n
        std = math.sqrt(variance) if variance > 0 else 0.0
        stats[name] = (mean, std)

    new_vectors = []
    for vec in vectors:
        new_features = {}
        for name in names:
            val = vec.features.get(name, 0.0)
            mean, std = stats[name]
            new_features[name] = round((val - mean) / std, 6) if std > 0 else 0.0
        new_vectors.append(
            FeatureVector(
                symbol=vec.symbol,
                timestamp=vec.timestamp,
                features=new_features,
            )
        )

    return new_vectors


def _normalize_min_max(
    vectors: list[FeatureVector],
    names: list[str],
    n: int,
) -> list[FeatureVector]:
    """min-max 标准化"""
    stats: dict[str, tuple[float, float]] = {}
    for name in names:
        values = [v.features.get(name, 0.0) for v in vectors]
        min_val = min(values)
        max_val = max(values)
        stats[name] = (min_val, max_val)

    new_vectors = []
    for vec in vectors:
        new_features = {}
        for name in names:
            val = vec.features.get(name, 0.0)
            min_val, max_val = stats[name]
            rng = max_val - min_val
            new_features[name] = (
                round((val - min_val) / rng, 6) if rng > 0 else 0.0
            )
        new_vectors.append(
            FeatureVector(
                symbol=vec.symbol,
                timestamp=vec.timestamp,
                features=new_features,
            )
        )

    return new_vectors


def to_csv_rows(matrix: FeatureMatrix) -> list[dict]:
    """转为 CSV 兼容的字典列表

    每行: {"symbol": "BTCUSDT", "timestamp": "2026-01-01T00:00:00", "rsi": 45.0, ...}
    """
    rows = []
    for vec in matrix.vectors:
        row: dict = {
            "symbol": vec.symbol,
            "timestamp": vec.timestamp,
        }
        for name in matrix.feature_names:
            row[name] = vec.features.get(name, 0.0)
        rows.append(row)
    return rows
