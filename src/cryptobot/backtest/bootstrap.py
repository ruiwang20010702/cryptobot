"""Bootstrap 置信区间

Percentile bootstrap 方法计算统计指标的置信区间。
纯 Python 实现，不依赖 numpy/scipy。
"""

import math
import random
from dataclasses import dataclass


@dataclass(frozen=True)
class ConfidenceInterval:
    """Bootstrap 置信区间结果"""

    point_estimate: float
    lower: float
    upper: float
    confidence_level: float  # e.g. 0.95
    n_samples: int
    n_bootstrap: int


def _calc_mean(values: list[float]) -> float:
    """计算均值"""
    return sum(values) / len(values)


def _calc_median(values: list[float]) -> float:
    """计算中位数"""
    s = sorted(values)
    n = len(s)
    mid = n // 2
    if n % 2 == 0:
        return (s[mid - 1] + s[mid]) / 2
    return s[mid]


def _calc_win_rate(values: list[float]) -> float:
    """计算胜率 (> 0 的比例)"""
    wins = sum(1 for v in values if v > 0)
    return wins / len(values)


def _calc_statistic(
    values: list[float],
    statistic: str,
) -> float:
    """根据 statistic 类型计算统计量

    Args:
        values: 数据列表 (非空)
        statistic: "mean" | "median" | "win_rate"

    Returns:
        统计量值
    """
    if statistic == "mean":
        return _calc_mean(values)
    if statistic == "median":
        return _calc_median(values)
    if statistic == "win_rate":
        return _calc_win_rate(values)
    msg = f"未知 statistic 类型: {statistic}"
    raise ValueError(msg)


def _percentile(sorted_values: list[float], p: float) -> float:
    """从已排序列表中取第 p 百分位数 (0.0-1.0)"""
    n = len(sorted_values)
    idx = p * (n - 1)
    lo = int(math.floor(idx))
    hi = int(math.ceil(idx))
    if lo == hi:
        return sorted_values[lo]
    frac = idx - lo
    return sorted_values[lo] * (1 - frac) + sorted_values[hi] * frac


_MIN_SAMPLES = 5


def bootstrap_ci(
    values: list[float],
    statistic: str = "mean",
    confidence: float = 0.95,
    n_bootstrap: int = 5000,
    seed: int = 42,
) -> ConfidenceInterval | None:
    """Percentile bootstrap 置信区间 (纯 Python, 无 numpy)

    Args:
        values: 数据点列表
        statistic: 统计量类型 ("mean", "median", "win_rate")
        confidence: 置信水平 (0.0-1.0)
        n_bootstrap: 重抽样次数
        seed: 随机种子 (确保可复现)

    Returns:
        ConfidenceInterval 或 None (数据不足时)
    """
    if not values or len(values) < _MIN_SAMPLES:
        return None

    rng = random.Random(seed)
    n = len(values)
    point_est = _calc_statistic(values, statistic)

    # 重抽样并计算每次的统计量
    boot_stats: list[float] = []
    for _ in range(n_bootstrap):
        sample = rng.choices(values, k=n)
        boot_stats.append(_calc_statistic(sample, statistic))

    boot_stats.sort()

    # 取 alpha/2 和 1-alpha/2 分位数
    alpha = 1.0 - confidence
    lower = _percentile(boot_stats, alpha / 2)
    upper = _percentile(boot_stats, 1 - alpha / 2)

    return ConfidenceInterval(
        point_estimate=round(point_est, 6),
        lower=round(lower, 6),
        upper=round(upper, 6),
        confidence_level=confidence,
        n_samples=n,
        n_bootstrap=n_bootstrap,
    )


def _std(values: list[float]) -> float:
    """样本标准差"""
    n = len(values)
    if n < 2:
        return 0.0
    mean = sum(values) / n
    variance = sum((x - mean) ** 2 for x in values) / (n - 1)
    return math.sqrt(variance)


def _boot_sharpe(values: list[float]) -> float:
    """计算 Sharpe ratio (统一年化函数)"""
    from cryptobot.backtest._sharpe_utils import annualize_sharpe

    return annualize_sharpe(values)


def _boot_profit_factor(values: list[float]) -> float:
    """计算 profit factor: sum(正值) / abs(sum(负值))"""
    wins = sum(v for v in values if v > 0)
    losses = abs(sum(v for v in values if v < 0))
    if losses == 0:
        return float("inf")
    if wins == 0:
        return 0.0
    return wins / losses


def _bootstrap_custom_ci(
    values: list[float],
    stat_fn,
    confidence: float = 0.95,
    n_bootstrap: int = 5000,
    seed: int = 42,
) -> ConfidenceInterval | None:
    """使用自定义 stat_fn 做 bootstrap CI

    Args:
        values: 数据列表
        stat_fn: 接受 list[float] 返回 float 的函数
        confidence: 置信水平
        n_bootstrap: 重抽样次数
        seed: 随机种子

    Returns:
        ConfidenceInterval 或 None
    """
    if not values or len(values) < _MIN_SAMPLES:
        return None

    rng = random.Random(seed)
    n = len(values)
    point_est = stat_fn(values)

    # inf 的点估计仍然返回 CI，但标记 point_estimate
    boot_stats: list[float] = []
    for _ in range(n_bootstrap):
        sample = rng.choices(values, k=n)
        val = stat_fn(sample)
        boot_stats.append(val)

    # 过滤掉 inf 值再排序取分位
    finite = [v for v in boot_stats if math.isfinite(v)]
    if len(finite) < n_bootstrap * 0.5:
        # 超过一半是 inf，CI 无意义
        if math.isfinite(point_est):
            return ConfidenceInterval(
                point_estimate=round(point_est, 6),
                lower=round(point_est, 6),
                upper=round(point_est, 6),
                confidence_level=confidence,
                n_samples=n,
                n_bootstrap=n_bootstrap,
            )
        return None

    finite.sort()
    alpha = 1.0 - confidence
    lower = _percentile(finite, alpha / 2)
    upper = _percentile(finite, 1 - alpha / 2)

    pe = point_est if math.isfinite(point_est) else upper
    return ConfidenceInterval(
        point_estimate=round(pe, 6),
        lower=round(lower, 6),
        upper=round(upper, 6),
        confidence_level=confidence,
        n_samples=n,
        n_bootstrap=n_bootstrap,
    )


def bootstrap_metric_ci(
    trades_pnl: list[float],
    confidence: float = 0.95,
) -> dict[str, ConfidenceInterval | None]:
    """批量计算: win_rate, avg_pnl, sharpe, profit_factor 的 CI

    Args:
        trades_pnl: 每笔交易的盈亏百分比列表
        confidence: 置信水平

    Returns:
        {"win_rate_ci": CI, "avg_pnl_ci": CI,
         "sharpe_ci": CI, "profit_factor_ci": CI}
    """
    return {
        "win_rate_ci": bootstrap_ci(
            trades_pnl, "win_rate", confidence=confidence,
        ),
        "avg_pnl_ci": bootstrap_ci(
            trades_pnl, "mean", confidence=confidence,
        ),
        "sharpe_ci": _bootstrap_custom_ci(
            trades_pnl, _boot_sharpe, confidence=confidence,
        ),
        "profit_factor_ci": _bootstrap_custom_ci(
            trades_pnl, _boot_profit_factor, confidence=confidence,
        ),
    }
