"""净值曲线追踪与回测指标测试"""

from dataclasses import dataclass

import pytest

from cryptobot.backtest.equity_tracker import (
    build_equity_curve,
    calc_metrics,
)


@dataclass(frozen=True)
class FakeTrade:
    """模拟交易结果"""

    net_pnl_pct: float
    exit_time: str
    gross_pnl_pct: float = 0.0
    net_pnl_usdt: float = 0.0


# ── 1. 空交易列表 ──


class TestEmptyTrades:
    def test_empty_curve(self):
        curve = build_equity_curve([])
        assert curve == []

    def test_empty_metrics(self):
        m = calc_metrics([], [])
        assert m.total_trades == 0
        assert m.win_rate == 0.0
        assert m.sharpe_ratio == 0.0
        assert m.max_drawdown_pct == 0.0
        assert m.monthly_returns == {}


# ── 2. 单笔盈利交易 ──


class TestSingleWin:
    def test_single_trade_equity(self):
        trades = [FakeTrade(net_pnl_pct=5.0, exit_time="2026-01-15T12:00:00")]
        curve = build_equity_curve(trades, initial_capital=10000)
        assert len(curve) == 1
        assert curve[0].equity == pytest.approx(10500.0, abs=0.01)
        assert curve[0].drawdown_pct == 0.0
        assert curve[0].trade_count == 1

    def test_single_trade_metrics(self):
        trades = [FakeTrade(net_pnl_pct=5.0, exit_time="2026-01-15T12:00:00")]
        curve = build_equity_curve(trades, initial_capital=10000)
        m = calc_metrics(curve, trades, initial_capital=10000)
        assert m.total_trades == 1
        assert m.win_rate == 1.0
        assert m.total_return_pct == pytest.approx(5.0, abs=0.01)
        assert m.max_drawdown_pct == 0.0
        # 单笔交易 Sharpe/Sortino 应为 0 (不够样本)
        assert m.sharpe_ratio == 0.0
        assert m.sortino_ratio == 0.0


# ── 3. 全胜场景 ──


class TestAllWins:
    def setup_method(self):
        self.trades = [
            FakeTrade(net_pnl_pct=3.0, exit_time="2026-01-10T10:00:00"),
            FakeTrade(net_pnl_pct=2.0, exit_time="2026-01-12T10:00:00"),
            FakeTrade(net_pnl_pct=4.0, exit_time="2026-01-14T10:00:00"),
            FakeTrade(net_pnl_pct=1.5, exit_time="2026-01-16T10:00:00"),
            FakeTrade(net_pnl_pct=2.5, exit_time="2026-01-18T10:00:00"),
        ]
        self.curve = build_equity_curve(self.trades, initial_capital=10000)
        self.metrics = calc_metrics(self.curve, self.trades, initial_capital=10000)

    def test_all_win_rate(self):
        assert self.metrics.win_rate == 1.0

    def test_all_win_sharpe_positive(self):
        assert self.metrics.sharpe_ratio > 0

    def test_all_win_no_drawdown(self):
        assert self.metrics.max_drawdown_pct == 0.0
        for pt in self.curve:
            assert pt.drawdown_pct == 0.0

    def test_all_win_equity_growth(self):
        assert self.curve[-1].equity > 10000

    def test_all_win_sortino_zero(self):
        # 全盈利无负收益 → Sortino = 0 (无 downside deviation)
        assert self.metrics.sortino_ratio == 0.0


# ── 4. 全败场景 ──


class TestAllLosses:
    def setup_method(self):
        self.trades = [
            FakeTrade(net_pnl_pct=-2.0, exit_time="2026-01-10T10:00:00"),
            FakeTrade(net_pnl_pct=-3.0, exit_time="2026-01-12T10:00:00"),
            FakeTrade(net_pnl_pct=-1.5, exit_time="2026-01-14T10:00:00"),
            FakeTrade(net_pnl_pct=-4.0, exit_time="2026-01-16T10:00:00"),
            FakeTrade(net_pnl_pct=-2.5, exit_time="2026-01-18T10:00:00"),
        ]
        self.curve = build_equity_curve(self.trades, initial_capital=10000)
        self.metrics = calc_metrics(self.curve, self.trades, initial_capital=10000)

    def test_all_loss_win_rate(self):
        assert self.metrics.win_rate == 0.0

    def test_all_loss_sharpe_negative(self):
        assert self.metrics.sharpe_ratio < 0

    def test_all_loss_equity_shrinks(self):
        assert self.curve[-1].equity < 10000

    def test_all_loss_max_drawdown_positive(self):
        assert self.metrics.max_drawdown_pct > 0


# ── 5. 混合场景: 交替盈亏 ──


class TestMixed:
    def setup_method(self):
        self.trades = [
            FakeTrade(net_pnl_pct=5.0, exit_time="2026-01-10T10:00:00"),
            FakeTrade(net_pnl_pct=-3.0, exit_time="2026-01-12T10:00:00"),
            FakeTrade(net_pnl_pct=4.0, exit_time="2026-01-14T10:00:00"),
            FakeTrade(net_pnl_pct=-2.0, exit_time="2026-01-16T10:00:00"),
            FakeTrade(net_pnl_pct=6.0, exit_time="2026-01-18T10:00:00"),
        ]
        self.curve = build_equity_curve(self.trades, initial_capital=10000)
        self.metrics = calc_metrics(self.curve, self.trades, initial_capital=10000)

    def test_mixed_correct_equity(self):
        # 10000 * 1.05 * 0.97 * 1.04 * 0.98 * 1.06
        expected = 10000 * 1.05 * 0.97 * 1.04 * 0.98 * 1.06
        assert self.curve[-1].equity == pytest.approx(expected, rel=1e-3)

    def test_mixed_drawdown_after_loss(self):
        # 第 2 笔是亏损，回撤应 > 0
        assert self.curve[1].drawdown_pct > 0

    def test_mixed_trade_count(self):
        assert self.curve[-1].trade_count == 5

    def test_mixed_win_rate(self):
        assert self.metrics.win_rate == pytest.approx(0.6, abs=0.01)


# ── 6. MaxDD 计算 ──


class TestMaxDrawdown:
    def test_known_drawdown(self):
        """构造: +10%, -15%, +5% → 峰值 11000, 最低 9350, DD ≈ 15%"""
        trades = [
            FakeTrade(net_pnl_pct=10.0, exit_time="2026-02-01T10:00:00"),
            FakeTrade(net_pnl_pct=-15.0, exit_time="2026-02-03T10:00:00"),
            FakeTrade(net_pnl_pct=5.0, exit_time="2026-02-05T10:00:00"),
        ]
        curve = build_equity_curve(trades, initial_capital=10000)
        metrics = calc_metrics(curve, trades, initial_capital=10000)

        # peak = 10000 * 1.10 = 11000
        # after -15%: 11000 * 0.85 = 9350
        # drawdown = (11000 - 9350) / 11000 * 100 = 15.0%
        assert metrics.max_drawdown_pct == pytest.approx(15.0, abs=0.01)

    def test_drawdown_recovery(self):
        """先跌后涨: -10%, +20% → 新高后回撤归零"""
        trades = [
            FakeTrade(net_pnl_pct=-10.0, exit_time="2026-02-01T10:00:00"),
            FakeTrade(net_pnl_pct=20.0, exit_time="2026-02-03T10:00:00"),
        ]
        curve = build_equity_curve(trades, initial_capital=10000)
        # 第 2 笔后创新高，回撤应为 0
        assert curve[-1].drawdown_pct == 0.0
        # 但 max_dd 应记录了第一笔时的回撤
        metrics = calc_metrics(curve, trades, initial_capital=10000)
        assert metrics.max_drawdown_pct == pytest.approx(10.0, abs=0.01)


# ── 7. 月度收益聚合 ──


class TestMonthlyReturns:
    def test_multi_month(self):
        trades = [
            FakeTrade(net_pnl_pct=5.0, exit_time="2026-01-15T10:00:00"),
            FakeTrade(net_pnl_pct=3.0, exit_time="2026-01-25T10:00:00"),
            FakeTrade(net_pnl_pct=-2.0, exit_time="2026-02-10T10:00:00"),
            FakeTrade(net_pnl_pct=4.0, exit_time="2026-02-20T10:00:00"),
        ]
        curve = build_equity_curve(trades, initial_capital=10000)
        metrics = calc_metrics(curve, trades, initial_capital=10000)

        assert "2026-01" in metrics.monthly_returns
        assert "2026-02" in metrics.monthly_returns

        # 1月: 10000 → 10000*1.05*1.03 = 10815 → ret ≈ 8.15%
        assert metrics.monthly_returns["2026-01"] == pytest.approx(8.15, abs=0.1)

        # 2月起始 10815: → 10815*0.98*1.04 = 10815*1.0192 = 11022.768
        jan_end = 10000 * 1.05 * 1.03
        feb_end = jan_end * 0.98 * 1.04
        expected_feb = (feb_end - jan_end) / jan_end * 100
        assert metrics.monthly_returns["2026-02"] == pytest.approx(expected_feb, abs=0.1)

    def test_single_month(self):
        trades = [
            FakeTrade(net_pnl_pct=2.0, exit_time="2026-03-05T10:00:00"),
            FakeTrade(net_pnl_pct=3.0, exit_time="2026-03-15T10:00:00"),
        ]
        curve = build_equity_curve(trades, initial_capital=10000)
        metrics = calc_metrics(curve, trades, initial_capital=10000)
        assert len(metrics.monthly_returns) == 1
        assert "2026-03" in metrics.monthly_returns


# ── 8. 年化收益计算 ──


class TestAnnualizedReturn:
    def test_annualized_positive(self):
        """30 天内 +10% → 年化应远大于 10%"""
        trades = [
            FakeTrade(net_pnl_pct=5.0, exit_time="2026-01-01T10:00:00"),
            FakeTrade(net_pnl_pct=5.0, exit_time="2026-01-31T10:00:00"),
        ]
        curve = build_equity_curve(trades, initial_capital=10000)
        metrics = calc_metrics(curve, trades, initial_capital=10000)
        # 30 天内 ~10.25% → 年化 >> 10%
        assert metrics.annualized_return_pct > 100

    def test_annualized_negative(self):
        """持续亏损 → 年化应为负"""
        trades = [
            FakeTrade(net_pnl_pct=-5.0, exit_time="2026-01-01T10:00:00"),
            FakeTrade(net_pnl_pct=-5.0, exit_time="2026-01-31T10:00:00"),
        ]
        curve = build_equity_curve(trades, initial_capital=10000)
        metrics = calc_metrics(curve, trades, initial_capital=10000)
        assert metrics.annualized_return_pct < 0

    def test_single_trade_zero_annualized(self):
        """单笔交易无法计算年化 (跨度=0) → 返回 0"""
        trades = [FakeTrade(net_pnl_pct=10.0, exit_time="2026-01-15T10:00:00")]
        curve = build_equity_curve(trades, initial_capital=10000)
        metrics = calc_metrics(curve, trades, initial_capital=10000)
        assert metrics.annualized_return_pct == 0.0


# ── 额外: 排序验证 ──


class TestSorting:
    def test_trades_sorted_by_exit_time(self):
        """传入乱序交易，曲线应按 exit_time 排序"""
        trades = [
            FakeTrade(net_pnl_pct=-3.0, exit_time="2026-01-20T10:00:00"),
            FakeTrade(net_pnl_pct=5.0, exit_time="2026-01-10T10:00:00"),
            FakeTrade(net_pnl_pct=2.0, exit_time="2026-01-15T10:00:00"),
        ]
        curve = build_equity_curve(trades, initial_capital=10000)
        timestamps = [p.timestamp for p in curve]
        assert timestamps == sorted(timestamps)
        # 第一笔应该是 +5%
        assert curve[0].trade_pnl_pct == 5.0
