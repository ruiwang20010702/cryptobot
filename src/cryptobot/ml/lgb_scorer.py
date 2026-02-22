"""LightGBM 信号评分

使用 LightGBM 对交易信号做概率评分，支持训练/评估/推理。
特征来自 feature_store，标签来自 K 线未来收益率。
"""

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from cryptobot.config import DATA_OUTPUT_DIR

logger = logging.getLogger(__name__)

MODELS_DIR = DATA_OUTPUT_DIR / "ml" / "models"


@dataclass(frozen=True)
class ModelMetrics:
    accuracy: float
    auc_roc: float
    precision: float
    recall: float
    f1: float
    feature_importance: dict[str, float]


@dataclass(frozen=True)
class SignalScore:
    symbol: str
    direction: str  # "up" | "down"
    probability: float  # 0.0 - 1.0
    model_version: str
    features_used: int


# ─── 训练数据准备 ─────────────────────────────────────────────────────


def prepare_training_data(
    days: int = 180,
    forward_hours: int = 24,
    threshold_pct: float = 1.0,
) -> tuple[list[dict], list[int]]:
    """准备训练数据：特征字典列表 + 二分类标签列表

    从 feature_store 加载历史特征，从 K 线计算未来收益率作为标签。
    标签: 未来 forward_hours 小时内收益率 > threshold_pct% → 1, 否则 → 0。
    """
    from cryptobot.features.feature_store import FEATURES_DIR, _load_from_file
    from cryptobot.indicators.calculator import load_klines

    if not FEATURES_DIR.exists():
        logger.warning("特征目录不存在: %s", FEATURES_DIR)
        return [], []

    # 加载日期范围内的特征文件
    cutoff = datetime.now(tz=timezone.utc) - timedelta(days=days)
    cutoff_str = cutoff.strftime("%Y-%m-%d")

    feature_files = sorted(FEATURES_DIR.glob("*.json"))
    feature_files = [f for f in feature_files if f.stem >= cutoff_str]

    if not feature_files:
        logger.warning("无可用特征文件 (近 %d 天)", days)
        return [], []

    # 按币种缓存 K 线
    klines_cache: dict[str, dict] = {}

    X: list[dict] = []
    y: list[int] = []

    for fpath in feature_files:
        matrix = _load_from_file(fpath)
        if matrix is None:
            continue

        for vec in matrix.vectors:
            symbol = vec.symbol
            features = vec.features
            if not features:
                continue

            # 获取 K 线数据计算未来收益率
            if symbol not in klines_cache:
                try:
                    df = load_klines(symbol, "1h")
                    # 转为 {timestamp_ms: close} 的映射便于快速查找
                    klines_cache[symbol] = {
                        int(ts.timestamp() * 1000): close
                        for ts, close in zip(df.index, df["close"])
                    }
                except Exception:
                    logger.debug("加载 K 线失败: %s", symbol)
                    klines_cache[symbol] = {}

            klines = klines_cache[symbol]
            if not klines:
                continue

            # 解析特征时间戳，找到对应的 K 线价格
            label = _compute_label(
                vec.timestamp, klines, forward_hours, threshold_pct
            )
            if label is None:
                continue

            X.append(features)
            y.append(label)

    logger.info("训练数据: %d 样本, 正例比例 %.1f%%",
                len(y), (sum(y) / len(y) * 100) if y else 0)
    return X, y


def _compute_label(
    timestamp_str: str,
    klines: dict[int, float],
    forward_hours: int,
    threshold_pct: float,
) -> int | None:
    """计算单个样本的标签

    返回 1 (涨) / 0 (不涨) / None (数据不足)。
    """
    try:
        ts = datetime.fromisoformat(timestamp_str.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return None

    ts_ms = int(ts.timestamp() * 1000)

    # 找到最接近的 K 线时间戳作为当前价格
    current_price = _find_nearest_price(klines, ts_ms, tolerance_ms=3600_000)
    if current_price is None:
        return None

    # 找未来 forward_hours 小时后的价格
    future_ms = ts_ms + forward_hours * 3600_000
    future_price = _find_nearest_price(klines, future_ms, tolerance_ms=3600_000)
    if future_price is None:
        return None

    # 计算收益率
    returns_pct = (future_price - current_price) / current_price * 100
    return 1 if returns_pct > threshold_pct else 0


def _find_nearest_price(
    klines: dict[int, float],
    target_ms: int,
    tolerance_ms: int,
) -> float | None:
    """在 klines 字典中找到最接近 target_ms 的价格"""
    if not klines:
        return None

    best_ts = None
    best_diff = float("inf")

    for ts_ms in klines:
        diff = abs(ts_ms - target_ms)
        if diff < best_diff:
            best_diff = diff
            best_ts = ts_ms

    if best_ts is not None and best_diff <= tolerance_ms:
        return klines[best_ts]
    return None


# ─── K-Fold 分割 ──────────────────────────────────────────────────────


def _kfold_split(
    n: int, n_folds: int,
) -> list[tuple[list[int], list[int]]]:
    """纯 Python K-fold 交叉验证索引分割

    返回 [(train_indices, val_indices), ...] 共 n_folds 组。
    """
    indices = list(range(n))
    fold_size = n // n_folds
    folds: list[tuple[list[int], list[int]]] = []

    for i in range(n_folds):
        start = i * fold_size
        end = start + fold_size if i < n_folds - 1 else n
        val_idx = indices[start:end]
        train_idx = indices[:start] + indices[end:]
        folds.append((train_idx, val_idx))

    return folds


# ─── 指标计算 (纯 Python) ─────────────────────────────────────────────


def _compute_metrics(
    y_true: list[int],
    y_pred: list[int],
    y_prob: list[float],
    feature_names: list[str],
    importance_values: list[float],
) -> ModelMetrics:
    """纯 Python 计算分类指标"""
    n = len(y_true)
    if n == 0:
        return ModelMetrics(
            accuracy=0.0, auc_roc=0.0, precision=0.0,
            recall=0.0, f1=0.0, feature_importance={},
        )

    # accuracy
    correct = sum(1 for a, b in zip(y_true, y_pred) if a == b)
    accuracy = correct / n

    # confusion matrix
    tp = sum(1 for a, b in zip(y_true, y_pred) if a == 1 and b == 1)
    fp = sum(1 for a, b in zip(y_true, y_pred) if a == 0 and b == 1)
    fn = sum(1 for a, b in zip(y_true, y_pred) if a == 1 and b == 0)

    precision = tp / (tp + fp) if (tp + fp) > 0 else 0.0
    recall = tp / (tp + fn) if (tp + fn) > 0 else 0.0
    f1 = (
        2 * precision * recall / (precision + recall)
        if (precision + recall) > 0
        else 0.0
    )

    # AUC-ROC (梯形法近似)
    auc_roc = _compute_auc_roc(y_true, y_prob)

    # feature importance
    importance = {}
    for name, val in zip(feature_names, importance_values):
        importance[name] = round(val, 4)

    return ModelMetrics(
        accuracy=round(accuracy, 4),
        auc_roc=round(auc_roc, 4),
        precision=round(precision, 4),
        recall=round(recall, 4),
        f1=round(f1, 4),
        feature_importance=importance,
    )


def _compute_auc_roc(y_true: list[int], y_prob: list[float]) -> float:
    """梯形法近似 AUC-ROC"""
    if not y_true or not y_prob:
        return 0.0

    n_pos = sum(y_true)
    n_neg = len(y_true) - n_pos
    if n_pos == 0 or n_neg == 0:
        return 0.5

    # 按概率降序排列
    pairs = sorted(zip(y_prob, y_true), key=lambda x: -x[0])

    tp = 0
    fp = 0
    prev_fpr = 0.0
    prev_tpr = 0.0
    auc = 0.0

    for prob, label in pairs:
        if label == 1:
            tp += 1
        else:
            fp += 1
        tpr = tp / n_pos
        fpr = fp / n_neg

        # 梯形面积
        auc += (fpr - prev_fpr) * (tpr + prev_tpr) / 2
        prev_fpr = fpr
        prev_tpr = tpr

    return auc


# ─── 模型训练 ─────────────────────────────────────────────────────────


def _dicts_to_matrix(
    dicts: list[dict], feature_names: list[str],
) -> list[list[float]]:
    """将特征字典列表转为二维列表 (样本 x 特征)"""
    return [
        [d.get(name, 0.0) for name in feature_names]
        for d in dicts
    ]


def train_model(
    X: list[dict],
    y: list[int],
    n_folds: int = 5,
) -> tuple[object, ModelMetrics]:
    """5-fold CV 训练 LightGBM 模型

    X: 特征字典列表
    y: 二分类标签列表
    返回: (最终模型 Booster, 平均指标 ModelMetrics)
    """
    import lightgbm as lgb

    if len(X) < n_folds:
        msg = f"样本数 ({len(X)}) 不足以做 {n_folds}-fold CV"
        raise ValueError(msg)

    # 统一特征名
    all_names: set[str] = set()
    for d in X:
        all_names.update(d.keys())
    feature_names = sorted(all_names)

    # 转为二维列表
    X_matrix = _dicts_to_matrix(X, feature_names)

    # LightGBM 参数
    params = {
        "objective": "binary",
        "metric": "binary_logloss",
        "verbosity": -1,
        "num_leaves": 31,
        "learning_rate": 0.05,
        "feature_fraction": 0.8,
        "bagging_fraction": 0.8,
        "bagging_freq": 5,
        "seed": 42,
    }

    # K-fold CV
    folds = _kfold_split(len(X_matrix), n_folds)

    all_y_true: list[int] = []
    all_y_pred: list[int] = []
    all_y_prob: list[float] = []
    total_importance = [0.0] * len(feature_names)

    for train_idx, val_idx in folds:
        X_train = [X_matrix[i] for i in train_idx]
        y_train = [y[i] for i in train_idx]
        X_val = [X_matrix[i] for i in val_idx]
        y_val = [y[i] for i in val_idx]

        train_data = lgb.Dataset(X_train, label=y_train, feature_name=feature_names)
        val_data = lgb.Dataset(X_val, label=y_val, reference=train_data)

        booster = lgb.train(
            params,
            train_data,
            num_boost_round=200,
            valid_sets=[val_data],
            callbacks=[lgb.early_stopping(20, verbose=False)],
        )

        # 预测
        probs = booster.predict(X_val)
        preds = [1 if p > 0.5 else 0 for p in probs]

        all_y_true.extend(y_val)
        all_y_pred.extend(preds)
        all_y_prob.extend(probs)

        # 特征重要性累加
        imp = booster.feature_importance(importance_type="gain")
        for j, v in enumerate(imp):
            total_importance[j] += v

    # 平均特征重要性
    avg_importance = [v / n_folds for v in total_importance]

    metrics = _compute_metrics(
        all_y_true, all_y_pred, all_y_prob,
        feature_names, avg_importance,
    )

    # 用全量数据训练最终模型
    full_data = lgb.Dataset(X_matrix, label=y, feature_name=feature_names)
    final_model = lgb.train(params, full_data, num_boost_round=200)

    logger.info(
        "模型训练完成: accuracy=%.3f, AUC=%.3f, F1=%.3f",
        metrics.accuracy, metrics.auc_roc, metrics.f1,
    )
    return final_model, metrics


# ─── 信号评分 ─────────────────────────────────────────────────────────


def score_signal(
    symbol: str,
    features: dict[str, float],
    model: object | None = None,
) -> SignalScore:
    """对单个信号进行概率评分

    如果未提供 model，自动加载最新模型。
    """
    if model is None:
        model, version = load_latest_model()
    else:
        version = "unknown"

    # 获取模型的特征名列表
    feature_names = model.feature_name()
    features_used = sum(1 for name in feature_names if name in features)

    # 构建输入向量
    x = [[features.get(name, 0.0) for name in feature_names]]
    probs = model.predict(x)
    prob = float(probs[0])

    direction = "up" if prob > 0.5 else "down"

    return SignalScore(
        symbol=symbol,
        direction=direction,
        probability=round(prob, 4),
        model_version=version,
        features_used=features_used,
    )


# ─── 模型持久化 ───────────────────────────────────────────────────────


def save_model(model: object, version: str) -> str:
    """保存模型到 data/output/ml/models/{version}.txt

    返回保存路径。
    """
    MODELS_DIR.mkdir(parents=True, exist_ok=True)
    path = MODELS_DIR / f"{version}.txt"
    model.save_model(str(path))
    logger.info("模型已保存: %s", path)
    return str(path)


def load_latest_model() -> tuple[object, str]:
    """加载最新模型

    返回 (Booster, version)。
    """
    import lightgbm as lgb

    if not MODELS_DIR.exists():
        msg = f"模型目录不存在: {MODELS_DIR}"
        raise FileNotFoundError(msg)

    files = sorted(MODELS_DIR.glob("*.txt"), key=lambda f: f.stat().st_mtime, reverse=True)
    if not files:
        msg = "无可用模型"
        raise FileNotFoundError(msg)

    latest = files[0]
    version = latest.stem
    booster = lgb.Booster(model_file=str(latest))
    logger.info("加载模型: %s (version=%s)", latest, version)
    return booster, version
