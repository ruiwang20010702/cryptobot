"""多周期回放对比器测试"""

import pytest

from cryptobot.backtest.replay_comparator import (
    PeriodComparison,
    PeriodResult,
    _calc_cv,
    _grade_stability,
    compare_replay_periods,
)


class TestCalcCv:
    def test_identical_values(self):
        assert _calc_cv([1.0, 1.0, 1.0]) == 0.0

    def test_varied_values(self):
        cv = _calc_cv([1.0, 2.0, 3.0])
        assert cv > 0

    def test_single_value(self):
        assert _calc_cv([5.0]) == 0.0


class TestGradeStability:
    def test_grade_a(self):
        assert _grade_stability(0.10, 0.10) == "A"

    def test_grade_b(self):
        assert _grade_stability(0.20, 0.25) == "B"

    def test_grade_c(self):
        assert _grade_stability(0.40, 0.60) == "C"

    def test_grade_d(self):
        assert _grade_stability(0.60, 0.60) == "D"


class TestCompareReplayPeriods:
    def _make_report(
        self, days, sharpe, win_rate, dd, ret, pf, trades=50
    ):
        """创建模拟 BacktestReport"""
        from cryptobot.backtest.equity_tracker import BacktestMetrics

        class FakeReport:
            pass

        r = FakeReport()
        r.config = {"days": days}
        r.metrics = BacktestMetrics(
            total_trades=trades,
            win_rate=win_rate,
            profit_factor=pf,
            sharpe_ratio=sharpe,
            sortino_ratio=0,
            max_drawdown_pct=dd,
            calmar_ratio=0,
            total_return_pct=ret,
            annualized_return_pct=0,
            avg_trade_pnl_pct=0,
            best_trade_pct=0,
            worst_trade_pct=0,
            monthly_returns={},
        )
        return r

    def test_empty_reports(self):
        result = compare_replay_periods([])
        assert result.stability_grade == "D"
        assert len(result.warnings) > 0

    def test_stable_periods(self):
        reports = [
            self._make_report(90, 1.5, 0.60, 10, 20, 1.8),
            self._make_report(180, 1.4, 0.58, 12, 35, 1.7),
            self._make_report(365, 1.3, 0.57, 14, 60, 1.6),
        ]
        result = compare_replay_periods(reports)
        assert result.stability_grade in ("A", "B")
        assert len(result.periods) == 3

    def test_unstable_periods(self):
        reports = [
            self._make_report(90, 2.0, 0.70, 5, 30, 2.5),
            self._make_report(180, -0.5, 0.40, 35, -10, 0.7),
        ]
        result = compare_replay_periods(reports)
        assert result.stability_grade in ("C", "D")
        assert any("负 Sharpe" in w for w in result.warnings)

    def test_period_result_frozen(self):
        p = PeriodResult(
            days=90,
            total_trades=50,
            win_rate=0.6,
            sharpe_ratio=1.5,
            max_drawdown_pct=10,
            total_return_pct=20,
            profit_factor=1.8,
        )
        with pytest.raises(AttributeError):
            p.days = 180
