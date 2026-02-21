"""净值曲线追踪与回测统计指标

从交易结果构建净值曲线，计算 Sharpe/Sortino/MaxDD/Calmar 等指标。
纯 Python 实现，不依赖 numpy/scipy。
"""

import math
from dataclasses import dataclass


@dataclass(frozen=True)
class EquityPoint:
    """净值曲线上的一个点"""

    timestamp: str  # ISO format
    equity: float  # 当前净值
    trade_pnl_pct: float  # 该笔交易收益率%
    drawdown_pct: float  # 当前回撤%
    trade_count: int  # 累计交易笔数


@dataclass(frozen=True)
class BacktestMetrics:
    """回测统计指标"""

    total_trades: int
    win_rate: float
    profit_factor: float  # gross_profit / gross_loss
    sharpe_ratio: float
    sortino_ratio: float
    max_drawdown_pct: float
    calmar_ratio: float
    total_return_pct: float
    annualized_return_pct: float
    avg_trade_pnl_pct: float
    best_trade_pct: float
    worst_trade_pct: float
    monthly_returns: dict  # {"2026-01": 3.5, ...}


def build_equity_curve(
    trades: list,
    initial_capital: float = 10000,
) -> list[EquityPoint]:
    """从交易结果构建净值曲线

    Args:
        trades: TradeResult 列表，需要有 net_pnl_pct 和 exit_time 属性
        initial_capital: 初始资金

    Returns:
        按 exit_time 排序的净值曲线
    """
    if not trades:
        return []

    sorted_trades = sorted(trades, key=lambda t: t.exit_time)
    equity = initial_capital
    peak = equity
    curve: list[EquityPoint] = []

    for i, t in enumerate(sorted_trades):
        equity = equity * (1 + t.net_pnl_pct / 100)
        peak = max(peak, equity)
        dd_pct = (peak - equity) / peak * 100 if peak > 0 else 0.0

        curve.append(EquityPoint(
            timestamp=t.exit_time,
            equity=round(equity, 4),
            trade_pnl_pct=t.net_pnl_pct,
            drawdown_pct=round(dd_pct, 4),
            trade_count=i + 1,
        ))

    return curve


def calc_metrics(
    equity_curve: list[EquityPoint],
    trades: list,
    initial_capital: float = 10000,
) -> BacktestMetrics:
    """计算回测统计指标

    Args:
        equity_curve: build_equity_curve 输出
        trades: 原始交易列表
        initial_capital: 初始资金

    Returns:
        BacktestMetrics 指标
    """
    if not trades or not equity_curve:
        return _zero_metrics()

    returns = [t.net_pnl_pct for t in trades]
    n = len(returns)

    # 基础统计
    wins = [r for r in returns if r > 0]
    losses = [r for r in returns if r <= 0]
    win_rate = len(wins) / n

    gross_profit = sum(wins) if wins else 0.0
    gross_loss = abs(sum(losses)) if losses else 0.0
    profit_factor = gross_profit / gross_loss if gross_loss > 0 else float("inf")

    avg_pnl = sum(returns) / n
    best_trade = max(returns)
    worst_trade = min(returns)

    # 最大回撤
    max_dd = max((p.drawdown_pct for p in equity_curve), default=0.0)

    # 总收益率
    final_equity = equity_curve[-1].equity
    total_return_pct = (final_equity - initial_capital) / initial_capital * 100

    # 年化收益率 — 基于实际交易跨度天数
    total_days = _calc_trading_days(trades)
    if total_days > 0:
        growth = final_equity / initial_capital
        annualized_return_pct = (growth ** (365 / total_days) - 1) * 100
    else:
        annualized_return_pct = 0.0

    # 年化因子: 365 / 平均每笔交易间隔天数
    avg_days_per_trade = total_days / n if total_days > 0 else 1
    trades_per_year = 365 / avg_days_per_trade if avg_days_per_trade > 0 else n

    # Sharpe
    sharpe_ratio = _calc_sharpe(returns, trades_per_year)

    # Sortino
    sortino_ratio = _calc_sortino(returns, trades_per_year)

    # Calmar
    calmar_ratio = annualized_return_pct / max_dd if max_dd > 0 else 0.0

    # 月度收益
    monthly = _calc_monthly_returns(equity_curve, initial_capital)

    return BacktestMetrics(
        total_trades=n,
        win_rate=round(win_rate, 4),
        profit_factor=round(profit_factor, 4) if profit_factor != float("inf") else float("inf"),
        sharpe_ratio=round(sharpe_ratio, 4),
        sortino_ratio=round(sortino_ratio, 4),
        max_drawdown_pct=round(max_dd, 4),
        calmar_ratio=round(calmar_ratio, 4),
        total_return_pct=round(total_return_pct, 4),
        annualized_return_pct=round(annualized_return_pct, 4),
        monthly_returns=monthly,
        avg_trade_pnl_pct=round(avg_pnl, 4),
        best_trade_pct=round(best_trade, 4),
        worst_trade_pct=round(worst_trade, 4),
    )


def _zero_metrics() -> BacktestMetrics:
    """空交易时的全零指标"""
    return BacktestMetrics(
        total_trades=0,
        win_rate=0.0,
        profit_factor=0.0,
        sharpe_ratio=0.0,
        sortino_ratio=0.0,
        max_drawdown_pct=0.0,
        calmar_ratio=0.0,
        total_return_pct=0.0,
        annualized_return_pct=0.0,
        avg_trade_pnl_pct=0.0,
        best_trade_pct=0.0,
        worst_trade_pct=0.0,
        monthly_returns={},
    )


def _calc_trading_days(trades: list) -> float:
    """计算第一笔到最后一笔交易的跨度天数"""
    if len(trades) < 2:
        return 0.0

    sorted_trades = sorted(trades, key=lambda t: t.exit_time)
    first = sorted_trades[0].exit_time
    last = sorted_trades[-1].exit_time

    from datetime import datetime

    try:
        t0 = datetime.fromisoformat(first)
        t1 = datetime.fromisoformat(last)
        delta = (t1 - t0).total_seconds() / 86400
        return max(delta, 0.0)
    except (ValueError, TypeError):
        return 0.0


def _std(values: list[float]) -> float:
    """计算样本标准差"""
    n = len(values)
    if n < 2:
        return 0.0
    mean = sum(values) / n
    variance = sum((x - mean) ** 2 for x in values) / (n - 1)
    return math.sqrt(variance)


def _calc_sharpe(returns: list[float], trades_per_year: float) -> float:
    """Sharpe = mean(returns) / std(returns) * sqrt(trades_per_year)"""
    if len(returns) < 2:
        return 0.0
    mean_r = sum(returns) / len(returns)
    std_r = _std(returns)
    if std_r == 0:
        return 0.0
    return mean_r / std_r * math.sqrt(trades_per_year)


def _calc_sortino(returns: list[float], trades_per_year: float) -> float:
    """Sortino = mean(returns) / downside_std * sqrt(trades_per_year)"""
    if len(returns) < 2:
        return 0.0
    mean_r = sum(returns) / len(returns)
    negatives = [r for r in returns if r < 0]
    if not negatives:
        return 0.0
    downside_std = _std(negatives) if len(negatives) >= 2 else abs(negatives[0])
    if downside_std == 0:
        return 0.0
    return mean_r / downside_std * math.sqrt(trades_per_year)


def _calc_monthly_returns(
    curve: list[EquityPoint],
    initial_capital: float,
) -> dict[str, float]:
    """按月聚合收益率 (%)"""
    if not curve:
        return {}

    # 按 YYYY-MM 分组，取每月最后一笔的 equity
    monthly_equity: dict[str, float] = {}
    for pt in curve:
        month_key = pt.timestamp[:7]  # "YYYY-MM"
        monthly_equity[month_key] = pt.equity

    result: dict[str, float] = {}
    months = sorted(monthly_equity.keys())
    prev_equity = initial_capital
    for m in months:
        end_equity = monthly_equity[m]
        ret_pct = (end_equity - prev_equity) / prev_equity * 100 if prev_equity > 0 else 0.0
        result[m] = round(ret_pct, 4)
        prev_equity = end_equity

    return result
