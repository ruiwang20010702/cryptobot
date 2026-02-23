"""Sharpe 年化统一工具

所有回测模块统一调用此函数计算年化 Sharpe，避免各处因子不一致。

年化公式: Sharpe = mean(returns) / std(returns) * sqrt(trades_per_year)
默认 trades_per_year 基于实际交易频率计算。
"""

import math


def annualize_sharpe(
    returns: list[float],
    trades_per_year: float | None = None,
) -> float:
    """计算年化 Sharpe ratio

    Args:
        returns: 每笔交易收益率列表 (%)
        trades_per_year: 年化交易笔数。None 时默认 252 (日度)。

    Returns:
        年化 Sharpe ratio
    """
    if len(returns) < 2:
        return 0.0

    n = len(returns)
    mean_r = sum(returns) / n
    variance = sum((x - mean_r) ** 2 for x in returns) / (n - 1)
    std_r = math.sqrt(variance)

    if std_r < 1e-10:
        return 0.0

    if trades_per_year is None:
        trades_per_year = 252.0

    return mean_r / std_r * math.sqrt(trades_per_year)
