"""多周期回放对比器

对比不同时间周期 (90/180/365天) 的回放结果，
评估策略在不同市场环境下的稳定性。
"""

import math
from dataclasses import dataclass, field


@dataclass(frozen=True)
class PeriodResult:
    """单个周期的关键指标"""

    days: int
    total_trades: int
    win_rate: float
    sharpe_ratio: float
    max_drawdown_pct: float
    total_return_pct: float
    profit_factor: float


@dataclass(frozen=True)
class PeriodComparison:
    """多周期对比结果"""

    periods: list[PeriodResult]
    sharpe_cv: float  # Sharpe 变异系数 (CV)，越低越稳定
    win_rate_cv: float  # 胜率变异系数
    return_cv: float  # 收益变异系数
    stability_grade: str  # A/B/C/D
    regime_breakdown: dict  # 按 regime 分组统计 (可选)
    warnings: list[str] = field(default_factory=list)


def _calc_cv(values: list[float]) -> float:
    """计算变异系数 (Coefficient of Variation)"""
    if len(values) < 2:
        return 0.0
    mean = sum(values) / len(values)
    if mean == 0:
        return 0.0
    variance = sum((x - mean) ** 2 for x in values) / (len(values) - 1)
    std = math.sqrt(variance)
    return abs(std / mean)


def _grade_stability(sharpe_cv: float, win_rate_cv: float) -> str:
    """根据 CV 评定稳定性等级

    A: 两项 CV < 0.15 (非常稳定)
    B: 两项 CV < 0.30 (较稳定)
    C: 任一 CV < 0.50 (不稳定)
    D: 两项 CV >= 0.50 (高度不稳定)
    """
    if sharpe_cv < 0.15 and win_rate_cv < 0.15:
        return "A"
    if sharpe_cv < 0.30 and win_rate_cv < 0.30:
        return "B"
    if sharpe_cv < 0.50 or win_rate_cv < 0.50:
        return "C"
    return "D"


def compare_replay_periods(reports: list) -> PeriodComparison:
    """对比多个回放报告

    Args:
        reports: BacktestReport 列表 (不同周期)

    Returns:
        PeriodComparison 对比结果
    """
    if not reports:
        return PeriodComparison(
            periods=[],
            sharpe_cv=0,
            win_rate_cv=0,
            return_cv=0,
            stability_grade="D",
            regime_breakdown={},
            warnings=["无报告数据"],
        )

    periods = []
    for r in reports:
        m = r.metrics
        days = (
            r.config.get("days", 0)
            if isinstance(r.config, dict)
            else 0
        )
        periods.append(
            PeriodResult(
                days=days,
                total_trades=m.total_trades,
                win_rate=m.win_rate,
                sharpe_ratio=m.sharpe_ratio,
                max_drawdown_pct=m.max_drawdown_pct,
                total_return_pct=m.total_return_pct,
                profit_factor=m.profit_factor,
            )
        )

    sharpes = [p.sharpe_ratio for p in periods if p.total_trades > 0]
    win_rates = [p.win_rate for p in periods if p.total_trades > 0]
    returns = [
        p.total_return_pct for p in periods if p.total_trades > 0
    ]

    sharpe_cv = _calc_cv(sharpes) if len(sharpes) >= 2 else 0.0
    wr_cv = _calc_cv(win_rates) if len(win_rates) >= 2 else 0.0
    ret_cv = _calc_cv(returns) if len(returns) >= 2 else 0.0

    grade = _grade_stability(sharpe_cv, wr_cv)

    # 警告
    warnings: list[str] = []
    if sharpe_cv > 0.5:
        warnings.append(
            f"Sharpe 变异系数 {sharpe_cv:.2f} 过高，策略稳定性差"
        )
    if any(p.sharpe_ratio < 0 for p in periods if p.total_trades > 0):
        warnings.append(
            "存在负 Sharpe 周期，策略可能在特定市场环境下亏损"
        )
    if any(p.max_drawdown_pct > 30 for p in periods):
        warnings.append("存在 >30% 最大回撤的周期")

    # regime 统计 (从 by_direction 中提取，简化版)
    regime_breakdown: dict = {}

    return PeriodComparison(
        periods=periods,
        sharpe_cv=round(sharpe_cv, 4),
        win_rate_cv=round(wr_cv, 4),
        return_cv=round(ret_cv, 4),
        stability_grade=grade,
        regime_breakdown=regime_breakdown,
        warnings=warnings,
    )
