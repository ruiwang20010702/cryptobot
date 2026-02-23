"""统计检验

Welch's t-test 和 Permutation test，用于比较 AI 信号与基线的显著性。
不依赖 scipy，纯 Python 实现。
"""

import math
import random
from dataclasses import dataclass


@dataclass(frozen=True)
class ComparisonResult:
    """策略对比结果"""

    ai_sharpe: float
    baseline_sharpe: float
    ai_mean_pnl: float
    baseline_mean_pnl: float
    pnl_p_value: float  # Welch's t-test p-value
    perm_p_value: float  # Permutation test p-value
    significant: bool  # p < 0.05
    n_ai: int
    n_baseline: int
    baseline_name: str


def compare_with_baseline(
    ai_trades: list,
    baseline_trades: list,
    baseline_name: str = "random",
) -> ComparisonResult:
    """比较 AI 交易与基线交易的收益差异

    ai_trades, baseline_trades: 具有 net_pnl_pct 属性的对象列表
    """
    ai_pnls = [t.net_pnl_pct for t in ai_trades]
    bl_pnls = [t.net_pnl_pct for t in baseline_trades]

    ai_mean = sum(ai_pnls) / len(ai_pnls) if ai_pnls else 0.0
    bl_mean = sum(bl_pnls) / len(bl_pnls) if bl_pnls else 0.0

    p_welch = _welch_t_test(ai_pnls, bl_pnls)
    p_perm = run_permutation_test(ai_pnls, bl_pnls)

    return ComparisonResult(
        ai_sharpe=_calc_sharpe(ai_pnls),
        baseline_sharpe=_calc_sharpe(bl_pnls),
        ai_mean_pnl=round(ai_mean, 4),
        baseline_mean_pnl=round(bl_mean, 4),
        pnl_p_value=round(p_welch, 4),
        perm_p_value=round(p_perm, 4),
        significant=p_welch < 0.05,
        n_ai=len(ai_pnls),
        n_baseline=len(bl_pnls),
        baseline_name=baseline_name,
    )


def _welch_t_test(sample1: list[float], sample2: list[float]) -> float:
    """Welch's t-test, 返回 p-value

    t = (mean1 - mean2) / sqrt(var1/n1 + var2/n2)
    自由度: Welch-Satterthwaite 公式
    p-value: Hill's approximation for t-distribution CDF
    """
    n1, n2 = len(sample1), len(sample2)
    if n1 < 2 or n2 < 2:
        return 1.0

    mean1 = sum(sample1) / n1
    mean2 = sum(sample2) / n2
    var1 = sum((x - mean1) ** 2 for x in sample1) / (n1 - 1)
    var2 = sum((x - mean2) ** 2 for x in sample2) / (n2 - 1)

    se = math.sqrt(var1 / n1 + var2 / n2)
    if se < 1e-10:
        return 1.0 if abs(mean1 - mean2) < 1e-10 else 0.0

    t_stat = (mean1 - mean2) / se

    # Welch-Satterthwaite 自由度
    v1 = var1 / n1
    v2 = var2 / n2
    df_num = (v1 + v2) ** 2
    df_den = v1**2 / (n1 - 1) + v2**2 / (n2 - 1)
    df = df_num / df_den if df_den > 0 else 2.0

    # t-distribution CDF 近似 (双尾 p-value)
    p_value = _t_distribution_p_value(abs(t_stat), df)
    return min(1.0, p_value)


def _t_distribution_p_value(t: float, df: float) -> float:
    """Hill's approximation for two-tailed p-value of t-distribution

    对于大自由度趋近正态分布，小自由度给出更保守的 p-value。
    """
    if df <= 0:
        return 1.0
    if t <= 0:
        return 1.0

    # 使用 regularized incomplete beta function 近似
    # I_x(a, b) where x = df/(df + t^2), a = df/2, b = 0.5
    x = df / (df + t * t)
    a = df / 2.0
    b = 0.5

    # 对于大 df (>100)，使用正态近似
    if df > 100:
        return math.erfc(t / math.sqrt(2))

    # 简化的 incomplete beta function 近似 (连分式展开)
    p = _regularized_incomplete_beta(x, a, b)
    return min(1.0, max(0.0, p))


def _regularized_incomplete_beta(x: float, a: float, b: float) -> float:
    """Regularized incomplete beta function I_x(a, b) 近似

    用于 t-distribution p-value 计算。
    采用连分式展开 (Lentz's method)。
    """
    if x <= 0:
        return 0.0
    if x >= 1:
        return 1.0

    # 使用 log-beta 避免大数溢出
    ln_beta = math.lgamma(a) + math.lgamma(b) - math.lgamma(a + b)
    front = math.exp(a * math.log(x) + b * math.log(1 - x) - ln_beta) / a

    # 连分式展开 (modified Lentz's method)
    # I_x(a, b) = front * cf, 其中 cf 是连分式
    max_iter = 200
    eps = 1e-14
    tiny = 1e-30

    # Evaluate the continued fraction
    c = 1.0
    d = 1.0 - (a + b) * x / (a + 1.0)
    if abs(d) < tiny:
        d = tiny
    d = 1.0 / d
    result = d

    for m in range(1, max_iter + 1):
        # Even step: d_{2m}
        numerator = m * (b - m) * x / ((a + 2 * m - 1) * (a + 2 * m))
        d = 1.0 + numerator * d
        if abs(d) < tiny:
            d = tiny
        c = 1.0 + numerator / c
        if abs(c) < tiny:
            c = tiny
        d = 1.0 / d
        result *= d * c

        # Odd step: d_{2m+1}
        numerator = -(a + m) * (a + b + m) * x / ((a + 2 * m) * (a + 2 * m + 1))
        d = 1.0 + numerator * d
        if abs(d) < tiny:
            d = tiny
        c = 1.0 + numerator / c
        if abs(c) < tiny:
            c = tiny
        d = 1.0 / d
        delta = d * c
        result *= delta

        if abs(delta - 1.0) < eps:
            break

    return front * result


def run_permutation_test(
    group1: list[float],
    group2: list[float],
    n_permutations: int = 10000,
    seed: int = 42,
) -> float:
    """非参数 permutation test

    合并两组数据，随机打散分组 n_permutations 次，
    计算观测到的差异在排列分布中的 p-value。
    """
    if not group1 or not group2:
        return 1.0

    observed_diff = abs(
        sum(group1) / len(group1) - sum(group2) / len(group2)
    )

    combined = group1 + group2
    n1 = len(group1)
    rng = random.Random(seed)

    count_extreme = 0
    for _ in range(n_permutations):
        rng.shuffle(combined)
        perm_g1 = combined[:n1]
        perm_g2 = combined[n1:]
        perm_diff = abs(
            sum(perm_g1) / len(perm_g1) - sum(perm_g2) / len(perm_g2)
        )
        if perm_diff >= observed_diff:
            count_extreme += 1

    return count_extreme / n_permutations


def _calc_sharpe(returns: list[float]) -> float:
    """计算 Sharpe ratio (统一年化函数)"""
    from cryptobot.backtest._sharpe_utils import annualize_sharpe

    return round(annualize_sharpe(returns), 4)
