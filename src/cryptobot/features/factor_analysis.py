"""多因子相关性分析

计算各特征因子与收益率在不同 lag 下的 Pearson 相关性，
找出最佳预测因子及其最优滞后期。
"""

import json
import logging
import math
from dataclasses import dataclass
from datetime import datetime, timezone
from cryptobot.config import DATA_OUTPUT_DIR
from cryptobot.features.feature_store import FEATURES_DIR
from cryptobot.features.pipeline import FeatureMatrix, FeatureVector
from cryptobot.risk.correlation import _pearson

logger = logging.getLogger(__name__)

_OUTPUT_PATH = DATA_OUTPUT_DIR / "evolution" / "factor_analysis.json"

DEFAULT_LAGS = [0, 4, 8, 12, 24]


@dataclass(frozen=True)
class FactorCorrelation:
    """单个因子在某 lag 下与收益率的相关性"""

    factor_name: str
    lag_hours: int  # 0, 4, 8, 12, 24
    correlation: float  # Pearson r
    p_value: float  # 统计显著性
    sample_size: int


@dataclass(frozen=True)
class FactorAnalysisResult:
    """因子分析完整结果"""

    factors: list[FactorCorrelation]
    top_predictors: list[FactorCorrelation]  # 按 |r| 排序，p<0.05
    optimal_lags: dict[str, int]  # factor_name -> best lag
    report: str


# ─── p-value 计算 ────────────────────────────────────────────────────────


def _pearson_p_value(r: float, n: int) -> float:
    """Pearson r 的 p-value (双尾)

    t = r * sqrt((n-2) / (1-r²))
    p = erfc(|t| / sqrt(2))
    """
    if n < 3:
        return 1.0
    r_sq = r * r
    if r_sq >= 1.0:
        return 0.0
    t_stat = r * math.sqrt((n - 2) / (1.0 - r_sq))
    return min(1.0, math.erfc(abs(t_stat) / math.sqrt(2)))


# ─── 核心计算 ────────────────────────────────────────────────────────────


def compute_lead_lag(
    feature_series: list[float],
    returns: list[float],
    lags: list[int] | None = None,
) -> list[FactorCorrelation]:
    """计算单个因子在不同 lag 下与收益率的相关性

    Args:
        feature_series: 因子时间序列（按时间升序）
        returns: 收益率时间序列（按时间升序，与 feature_series 对齐）
        lags: 滞后期列表（小时），默认 [0,4,8,12,24]

    Returns:
        每个 lag 对应一个 FactorCorrelation
    """
    if lags is None:
        lags = DEFAULT_LAGS

    results: list[FactorCorrelation] = []

    for lag in lags:
        if lag < 0:
            continue
        # lag=k 表示因子领先收益率 k 个时间步
        # 即 feature[i] 对应 returns[i+lag]
        n_available = len(feature_series) - lag
        n_ret = len(returns) - lag
        n = min(n_available, n_ret)

        if n < 3:
            results.append(FactorCorrelation(
                factor_name="",
                lag_hours=lag,
                correlation=0.0,
                p_value=1.0,
                sample_size=0,
            ))
            continue

        feat_slice = feature_series[:n]
        ret_slice = returns[lag: lag + n]

        r = _pearson(feat_slice, ret_slice)
        p = _pearson_p_value(r, n)

        results.append(FactorCorrelation(
            factor_name="",
            lag_hours=lag,
            correlation=round(r, 6),
            p_value=round(p, 6),
            sample_size=n,
        ))

    return results


def _extract_factor_series(
    vectors: list[FeatureVector],
    factor_name: str,
) -> list[float]:
    """从特征向量列表中提取单因子时间序列"""
    return [v.features.get(factor_name, 0.0) for v in vectors]


def _compute_returns(vectors: list[FeatureVector]) -> list[float]:
    """从特征向量中提取收盘价并计算收益率序列

    使用 'close' 或 'latest_close' 字段，若无则用 0 占位。
    返回与 vectors 等长的序列（第一个为 0.0）。
    """
    closes: list[float] = []
    for v in vectors:
        c = v.features.get("close", v.features.get("latest_close", 0.0))
        closes.append(c)

    if len(closes) < 2:
        return [0.0] * len(closes)

    returns = [0.0]  # 第一个时间点无收益率
    for i in range(1, len(closes)):
        if closes[i - 1] != 0:
            returns.append((closes[i] - closes[i - 1]) / closes[i - 1])
        else:
            returns.append(0.0)
    return returns


def _load_feature_matrices(days: int) -> list[FeatureMatrix]:
    """加载最近 N 天的特征矩阵文件"""
    if not FEATURES_DIR.exists():
        return []

    files = sorted(FEATURES_DIR.glob("*.json"), reverse=True)
    selected = files[:days]

    matrices: list[FeatureMatrix] = []
    for path in reversed(selected):  # 按时间升序
        try:
            data = json.loads(path.read_text())
            vectors = [
                FeatureVector(
                    symbol=v["symbol"],
                    timestamp=v["timestamp"],
                    features=v["features"],
                )
                for v in data.get("vectors", [])
            ]
            if vectors:
                feature_names = data.get("feature_names", [])
                matrices.append(FeatureMatrix(
                    vectors=vectors, feature_names=feature_names,
                ))
        except (json.JSONDecodeError, KeyError, OSError) as e:
            logger.warning("加载特征文件失败 %s: %s", path, e)
    return matrices


def run_factor_analysis(
    symbols: list[str] | None = None,
    days: int = 90,
    lags: list[int] | None = None,
) -> FactorAnalysisResult:
    """完整因子分析流程

    1. 加载最近 N 天特征矩阵
    2. 按 symbol 聚合时间序列
    3. 对每个因子计算各 lag 下的相关性
    4. 筛选 top predictors (p<0.05)
    5. 找出每个因子的最优 lag
    """
    if lags is None:
        lags = DEFAULT_LAGS

    matrices = _load_feature_matrices(days)
    if not matrices:
        empty_result = FactorAnalysisResult(
            factors=[],
            top_predictors=[],
            optimal_lags={},
            report="无可用特征数据",
        )
        return empty_result

    # 按 symbol 聚合所有 vectors（时间升序）
    sym_vectors: dict[str, list[FeatureVector]] = {}
    for m in matrices:
        for v in m.vectors:
            if symbols and v.symbol not in symbols:
                continue
            sym_vectors.setdefault(v.symbol, []).append(v)

    if not sym_vectors:
        return FactorAnalysisResult(
            factors=[],
            top_predictors=[],
            optimal_lags={},
            report="无匹配的币种数据",
        )

    # 收集所有因子名
    all_factor_names: set[str] = set()
    for m in matrices:
        all_factor_names.update(m.feature_names)
    # 排除价格本身
    all_factor_names.discard("close")
    all_factor_names.discard("latest_close")
    factor_names = sorted(all_factor_names)

    # 对每个因子，汇总所有 symbol 的 lead-lag 相关性
    all_factors: list[FactorCorrelation] = []

    for fname in factor_names:
        # 汇总所有 symbol 的序列
        all_feat: list[float] = []
        all_ret: list[float] = []
        for _sym, vecs in sym_vectors.items():
            feat_series = _extract_factor_series(vecs, fname)
            ret_series = _compute_returns(vecs)
            all_feat.extend(feat_series)
            all_ret.extend(ret_series)

        if len(all_feat) < 3:
            continue

        lag_results = compute_lead_lag(all_feat, all_ret, lags)
        for fc in lag_results:
            # 用实际的 factor_name 替换空占位
            all_factors.append(FactorCorrelation(
                factor_name=fname,
                lag_hours=fc.lag_hours,
                correlation=fc.correlation,
                p_value=fc.p_value,
                sample_size=fc.sample_size,
            ))

    # top predictors: p < 0.05, 按 |r| 降序
    top_predictors = sorted(
        [f for f in all_factors if f.p_value < 0.05],
        key=lambda f: abs(f.correlation),
        reverse=True,
    )

    # optimal lag: 每个因子的最大 |r| 对应的 lag
    optimal_lags: dict[str, int] = {}
    for fname in factor_names:
        factor_group = [f for f in all_factors if f.factor_name == fname]
        if factor_group:
            best = max(factor_group, key=lambda f: abs(f.correlation))
            optimal_lags[fname] = best.lag_hours

    report = _generate_report(all_factors, top_predictors, optimal_lags)

    result = FactorAnalysisResult(
        factors=all_factors,
        top_predictors=top_predictors,
        optimal_lags=optimal_lags,
        report=report,
    )

    _save_result(result)
    return result


# ─── 报告生成 ────────────────────────────────────────────────────────────


def _generate_report(
    factors: list[FactorCorrelation],
    top_predictors: list[FactorCorrelation],
    optimal_lags: dict[str, int],
) -> str:
    """生成因子分析文本报告"""
    lines: list[str] = []
    lines.append("=" * 60)
    lines.append("多因子相关性分析报告")
    lines.append("=" * 60)

    # 总览
    total_factors = len(set(f.factor_name for f in factors))
    total_pairs = len(factors)
    sig_count = len(top_predictors)
    lines.append(f"\n分析因子数: {total_factors}")
    lines.append(f"因子-Lag 组合: {total_pairs}")
    lines.append(f"显著预测因子 (p<0.05): {sig_count}")

    # Top 预测因子
    if top_predictors:
        lines.append(f"\n{'─' * 60}")
        lines.append("Top 预测因子 (按 |r| 排序)")
        lines.append(f"{'─' * 60}")
        lines.append(f"{'因子':<25} {'Lag(h)':>6} {'r':>8} {'p-value':>10} {'N':>6}")
        lines.append("-" * 60)
        for fc in top_predictors[:20]:
            lines.append(
                f"{fc.factor_name:<25} {fc.lag_hours:>6} "
                f"{fc.correlation:>8.4f} {fc.p_value:>10.4f} {fc.sample_size:>6}"
            )
    else:
        lines.append("\n无显著预测因子")

    # 最优 Lag
    if optimal_lags:
        lines.append(f"\n{'─' * 60}")
        lines.append("最优滞后期")
        lines.append(f"{'─' * 60}")
        for fname, lag in sorted(optimal_lags.items()):
            lines.append(f"  {fname:<25} → {lag}h")

    return "\n".join(lines)


# ─── 持久化 ──────────────────────────────────────────────────────────────


def _save_result(result: FactorAnalysisResult) -> None:
    """保存因子分析结果到 JSON"""
    try:
        _OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "computed_at": datetime.now(timezone.utc).isoformat(),
            "total_factors": len(set(f.factor_name for f in result.factors)),
            "significant_count": len(result.top_predictors),
            "optimal_lags": result.optimal_lags,
            "top_predictors": [
                {
                    "factor_name": f.factor_name,
                    "lag_hours": f.lag_hours,
                    "correlation": f.correlation,
                    "p_value": f.p_value,
                    "sample_size": f.sample_size,
                }
                for f in result.top_predictors[:30]
            ],
            "all_factors": [
                {
                    "factor_name": f.factor_name,
                    "lag_hours": f.lag_hours,
                    "correlation": f.correlation,
                    "p_value": f.p_value,
                    "sample_size": f.sample_size,
                }
                for f in result.factors
            ],
        }
        tmp = _OUTPUT_PATH.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False))
        tmp.rename(_OUTPUT_PATH)
        logger.info("因子分析结果已保存: %s", _OUTPUT_PATH)
    except Exception as e:
        logger.warning("因子分析结果保存失败: %s", e)
