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
    p-value: 用正态近似 (erfc)
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

    # 正态近似 p-value (双尾): erfc(|t| / sqrt(2))
    p_value = math.erfc(abs(t_stat) / math.sqrt(2))
    return min(1.0, p_value)


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
    """计算简单 Sharpe ratio: mean / std × sqrt(N_annual)

    假设每笔交易间隔约 12 小时，年化 = 365 * 2 = 730 笔。
    """
    if len(returns) < 2:
        return 0.0

    n = len(returns)
    mean = sum(returns) / n
    var = sum((x - mean) ** 2 for x in returns) / (n - 1)
    std = math.sqrt(var)

    if std < 1e-10:
        return 0.0

    annualization = math.sqrt(730)
    return round(mean / std * annualization, 4)
