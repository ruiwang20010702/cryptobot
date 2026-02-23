"""Walk-forward 滚动验证 -- 防止过拟合

核心思路:
1. 将历史数据切分为 训练集 + 测试集 的滚动窗口
2. 每个窗口: 训练集算指标 -> 测试集验证
3. 对比样本内 (IS) vs 样本外 (OOS) 的 Sharpe 退化程度
4. ratio < 2.0 视为通过（OOS 退化在可接受范围内）
"""

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class WalkForwardWindow:
    """单个滚动窗口"""

    train_start: str  # ISO format
    train_end: str
    test_start: str
    test_end: str


@dataclass(frozen=True)
class WindowResult:
    """单窗口回测结果"""

    window: WalkForwardWindow
    train_trades: int
    test_trades: int
    train_win_rate: float
    test_win_rate: float
    train_sharpe: float
    test_sharpe: float
    train_pnl_pct: float
    test_pnl_pct: float


@dataclass(frozen=True)
class WalkForwardResult:
    """Walk-forward 总结果"""

    windows: list[WindowResult]
    oos_sharpe: float  # 所有测试集平均 Sharpe
    is_sharpe: float  # 所有训练集平均 Sharpe
    is_vs_oos_ratio: float  # 样本内/外 Sharpe 比
    degradation_pct: float  # 样本外相对退化 %
    passed: bool  # ratio < 2.0 视为通过
    total_oos_trades: int
    total_is_trades: int
    summary: str  # 人类可读总结


# ── 窗口生成 ──────────────────────────────────────────────────────────────


def generate_windows(
    total_days: int,
    train_days: int = 60,
    test_days: int = 30,
    step_days: int = 30,
    end_date: datetime | None = None,
) -> list[WalkForwardWindow]:
    """生成滚动窗口列表

    Args:
        total_days: 总数据天数
        train_days: 训练窗口天数 (默认 60)
        test_days: 测试窗口天数 (默认 30)
        step_days: 步进天数 (默认 30)
        end_date: 结束日期 (默认今天)

    Returns:
        滚动窗口列表，从最早开始

    例如 180 天数据, 60d train + 30d test, 步进 30d:
    Window 1: train [0-60], test [60-90]
    Window 2: train [30-90], test [90-120]
    Window 3: train [60-120], test [120-150]
    Window 4: train [90-150], test [150-180]
    """
    if end_date is None:
        end_date = datetime.now(timezone.utc)

    start_date = end_date - timedelta(days=total_days)
    window_size = train_days + test_days

    windows: list[WalkForwardWindow] = []
    offset = 0
    while offset + window_size <= total_days:
        w_start = start_date + timedelta(days=offset)
        train_end = w_start + timedelta(days=train_days)
        test_end = train_end + timedelta(days=test_days)

        windows.append(
            WalkForwardWindow(
                train_start=w_start.isoformat(),
                train_end=train_end.isoformat(),
                test_start=train_end.isoformat(),
                test_end=test_end.isoformat(),
            )
        )
        offset += step_days

    return windows


# ── 核心运行 ──────────────────────────────────────────────────────────────


_MIN_TRADES_PER_WINDOW = 5


def run_walk_forward(
    trades: list,
    windows: list[WalkForwardWindow],
) -> WalkForwardResult:
    """对每个窗口分割交易并计算指标

    Args:
        trades: TradeResult 列表 (需要 entry_time, net_pnl_pct 等)
        windows: 滚动窗口列表

    核心逻辑:
    - 对每个窗口，按 entry_time 将交易分到训练集和测试集
    - 分别计算胜率和 Sharpe
    - 最后汇总 OOS vs IS 比率
    """
    if not windows:
        return _empty_result("无滚动窗口")

    results: list[WindowResult] = []

    for window in windows:
        train = _filter_trades_by_time(
            trades, window.train_start, window.train_end,
        )
        test = _filter_trades_by_time(
            trades, window.test_start, window.test_end,
        )

        train_wr = _calc_win_rate(train)
        test_wr = _calc_win_rate(test)
        train_sharpe = _calc_simple_sharpe(train)
        test_sharpe = _calc_simple_sharpe(test)
        train_pnl = sum(t.net_pnl_pct for t in train) if train else 0.0
        test_pnl = sum(t.net_pnl_pct for t in test) if test else 0.0

        results.append(
            WindowResult(
                window=window,
                train_trades=len(train),
                test_trades=len(test),
                train_win_rate=train_wr,
                test_win_rate=test_wr,
                train_sharpe=round(train_sharpe, 3),
                test_sharpe=round(test_sharpe, 3),
                train_pnl_pct=round(train_pnl, 2),
                test_pnl_pct=round(test_pnl, 2),
            )
        )

    # 汇总 (仅统计交易数达标的窗口)
    is_sharpes = [
        r.train_sharpe
        for r in results
        if r.train_trades >= _MIN_TRADES_PER_WINDOW
    ]
    oos_sharpes = [
        r.test_sharpe
        for r in results
        if r.test_trades >= _MIN_TRADES_PER_WINDOW
    ]

    avg_is = sum(is_sharpes) / len(is_sharpes) if is_sharpes else 0.0
    avg_oos = sum(oos_sharpes) / len(oos_sharpes) if oos_sharpes else 0.0

    ratio = _safe_ratio(avg_is, avg_oos)
    degradation = ((avg_is - avg_oos) / avg_is * 100) if avg_is > 0 else 0.0
    passed = ratio < 2.0 and avg_oos > 0

    total_oos = sum(r.test_trades for r in results)
    total_is = sum(r.train_trades for r in results)

    summary = _build_summary(passed, avg_oos, ratio, degradation)

    return WalkForwardResult(
        windows=results,
        oos_sharpe=round(avg_oos, 3),
        is_sharpe=round(avg_is, 3),
        is_vs_oos_ratio=round(ratio, 3),
        degradation_pct=round(degradation, 1),
        passed=passed,
        total_oos_trades=total_oos,
        total_is_trades=total_is,
        summary=summary,
    )


# ── 辅助函数 ──────────────────────────────────────────────────────────────


def _filter_trades_by_time(trades: list, start: str, end: str) -> list:
    """按 entry_time 过滤交易到 [start, end) 区间"""
    filtered = []
    # 截取到秒级别进行字符串比较 (忽略时区后缀差异)
    start_key = start[:19]
    end_key = end[:19]

    for t in trades:
        et = getattr(t, "entry_time", "")
        et_key = et[:19] if len(et) >= 19 else et
        if start_key <= et_key < end_key:
            filtered.append(t)

    return filtered


def _calc_win_rate(trades: list) -> float:
    """计算胜率"""
    if not trades:
        return 0.0
    wins = sum(1 for t in trades if t.net_pnl_pct > 0)
    return round(wins / len(trades), 3)


def _calc_simple_sharpe(trades: list) -> float:
    """简化 Sharpe (统一年化函数)"""
    if len(trades) < 2:
        return 0.0

    from cryptobot.backtest._sharpe_utils import annualize_sharpe

    pnls = [t.net_pnl_pct for t in trades]
    return annualize_sharpe(pnls)


def _safe_ratio(numerator: float, denominator: float) -> float:
    """安全除法: 避免除零"""
    if denominator > 0:
        return numerator / denominator
    if numerator > 0:
        return float("inf")
    return 1.0


def _build_summary(
    passed: bool, avg_oos: float, ratio: float, degradation: float,
) -> str:
    """生成人类可读总结"""
    if passed:
        return f"通过: OOS Sharpe {avg_oos:.2f} (退化 {degradation:.0f}%)"
    if avg_oos <= 0:
        return f"未通过: OOS Sharpe {avg_oos:.2f} <= 0, 策略样本外无效"
    return (
        f"未通过: IS/OOS 比 {ratio:.1f} >= 2.0, "
        f"疑似过拟合 (退化 {degradation:.0f}%)"
    )


def _empty_result(summary: str) -> WalkForwardResult:
    """空结果"""
    return WalkForwardResult(
        windows=[],
        oos_sharpe=0.0,
        is_sharpe=0.0,
        is_vs_oos_ratio=0.0,
        degradation_pct=0.0,
        passed=False,
        total_oos_trades=0,
        total_is_trades=0,
        summary=summary,
    )
