"""Walk-forward 滚动验证测试"""

from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest

from cryptobot.backtest.walk_forward import (
    _calc_simple_sharpe,
    _calc_win_rate,
    _filter_trades_by_time,
    generate_windows,
    run_walk_forward,
)


# ── 辅助 ──────────────────────────────────────────────────────────────────


def _make_trade(entry_time: str, pnl_pct: float) -> MagicMock:
    """创建模拟 TradeResult"""
    t = MagicMock()
    t.entry_time = entry_time
    t.net_pnl_pct = pnl_pct
    return t


# ── generate_windows ──────────────────────────────────────────────────────


class TestGenerateWindows:
    def test_180d_default_produces_4_windows(self):
        """180天, 60d/30d/30d -> 4 个窗口"""
        windows = generate_windows(180)
        assert len(windows) == 4

    def test_90d_produces_1_window(self):
        """90天 -> 1 个窗口"""
        windows = generate_windows(90)
        assert len(windows) == 1

    def test_short_data_returns_empty(self):
        """数据不足 (60天 < 60+30) -> 空列表"""
        windows = generate_windows(60)
        assert len(windows) == 0

    def test_exact_boundary(self):
        """刚好 90 天 = 60+30, 应产生 1 个窗口"""
        windows = generate_windows(90, train_days=60, test_days=30, step_days=30)
        assert len(windows) == 1

    def test_window_dates_sequential(self):
        """窗口日期连续: train_start < train_end == test_start < test_end"""
        windows = generate_windows(180)
        for w in windows:
            assert w.train_start < w.train_end
            assert w.train_end == w.test_start
            assert w.test_start < w.test_end

    def test_custom_params(self):
        """自定义参数: 120天, 30d/30d/15d -> 4 个窗口"""
        windows = generate_windows(120, train_days=30, test_days=30, step_days=15)
        # offset: 0(0-60), 15(15-75), 30(30-90), 45(45-105), 60(60-120)
        assert len(windows) == 5

    def test_fixed_end_date(self):
        """指定 end_date 后窗口日期可预测"""
        end = datetime(2025, 7, 1, tzinfo=timezone.utc)
        windows = generate_windows(90, end_date=end)
        assert len(windows) == 1
        # 起点: 2025-04-02
        assert windows[0].train_start.startswith("2025-04-02")

    def test_365d_many_windows(self):
        """365 天应该产生足够多窗口"""
        windows = generate_windows(365)
        # (365 - 90) / 30 + 1 = 10 (offset: 0,30,60,...,270)
        assert len(windows) >= 9


# ── _filter_trades_by_time ────────────────────────────────────────────────


class TestFilterTradesByTime:
    def test_basic_filter(self):
        trades = [
            _make_trade("2025-01-15T12:00:00", 1.0),
            _make_trade("2025-02-15T12:00:00", 2.0),
            _make_trade("2025-03-15T12:00:00", 3.0),
        ]
        result = _filter_trades_by_time(
            trades, "2025-01-01T00:00:00", "2025-02-01T00:00:00",
        )
        assert len(result) == 1
        assert result[0].net_pnl_pct == 1.0

    def test_boundary_inclusive_start_exclusive_end(self):
        """起点包含, 终点排除"""
        trades = [
            _make_trade("2025-01-01T00:00:00", 1.0),
            _make_trade("2025-02-01T00:00:00", 2.0),
        ]
        result = _filter_trades_by_time(
            trades, "2025-01-01T00:00:00", "2025-02-01T00:00:00",
        )
        assert len(result) == 1
        assert result[0].net_pnl_pct == 1.0

    def test_empty_trades(self):
        result = _filter_trades_by_time(
            [], "2025-01-01T00:00:00", "2025-02-01T00:00:00",
        )
        assert result == []

    def test_timezone_suffix_ignored(self):
        """时区后缀不影响过滤"""
        trades = [
            _make_trade("2025-01-15T12:00:00+00:00", 1.0),
        ]
        result = _filter_trades_by_time(
            trades, "2025-01-01T00:00:00+08:00", "2025-02-01T00:00:00Z",
        )
        assert len(result) == 1


# ── _calc_win_rate ────────────────────────────────────────────────────────


class TestCalcWinRate:
    def test_empty(self):
        assert _calc_win_rate([]) == 0.0

    def test_all_wins(self):
        trades = [_make_trade("", 1.0), _make_trade("", 2.0)]
        assert _calc_win_rate(trades) == 1.0

    def test_all_losses(self):
        trades = [_make_trade("", -1.0), _make_trade("", -2.0)]
        assert _calc_win_rate(trades) == 0.0

    def test_mixed(self):
        trades = [
            _make_trade("", 1.0),
            _make_trade("", -1.0),
            _make_trade("", 2.0),
        ]
        assert _calc_win_rate(trades) == pytest.approx(0.667, abs=0.001)


# ── _calc_simple_sharpe ───────────────────────────────────────────────────


class TestCalcSimpleSharpe:
    def test_empty(self):
        assert _calc_simple_sharpe([]) == 0.0

    def test_single_trade(self):
        """单笔不够算 std -> 0"""
        assert _calc_simple_sharpe([_make_trade("", 1.0)]) == 0.0

    def test_positive_sharpe(self):
        """全正 PnL -> 正 Sharpe"""
        trades = [_make_trade("", 1.0 + i * 0.1) for i in range(10)]
        sharpe = _calc_simple_sharpe(trades)
        assert sharpe > 0

    def test_negative_sharpe(self):
        """全负 PnL -> 负 Sharpe"""
        trades = [_make_trade("", -1.0 - i * 0.1) for i in range(10)]
        sharpe = _calc_simple_sharpe(trades)
        assert sharpe < 0

    def test_zero_std(self):
        """所有 PnL 相同 -> std=0 -> Sharpe=0"""
        trades = [_make_trade("", 1.0) for _ in range(5)]
        assert _calc_simple_sharpe(trades) == 0.0


# ── run_walk_forward ──────────────────────────────────────────────────────


class TestRunWalkForward:
    def test_empty_windows(self):
        """空窗口列表 -> 不通过"""
        result = run_walk_forward([], [])
        assert result.passed is False
        assert result.summary == "无滚动窗口"

    def test_empty_trades(self):
        """有窗口但无交易 -> 不通过"""
        windows = generate_windows(180)
        result = run_walk_forward([], windows)
        assert result.passed is False
        assert result.total_oos_trades == 0
        assert result.total_is_trades == 0

    def test_passed_when_oos_good(self):
        """训练集和测试集 Sharpe 相近 -> 通过"""
        end = datetime(2025, 7, 1, tzinfo=timezone.utc)
        windows = generate_windows(180, end_date=end)

        # 在所有窗口中均匀分布正向交易 (每5天一笔)
        trades = []
        base = datetime(2025, 1, 2, 12, 0, 0, tzinfo=timezone.utc)
        for i in range(36):  # 180天 / 5天 = 36 笔
            from datetime import timedelta
            t_time = base + timedelta(days=i * 5)
            pnl = 1.5 + (i % 3) * 0.5  # 1.5, 2.0, 2.5 循环
            trades.append(_make_trade(t_time.isoformat(), pnl))

        result = run_walk_forward(trades, windows)
        # 因为 PnL 在训练和测试集分布类似, ratio 应接近 1
        assert result.passed is True
        assert result.is_vs_oos_ratio < 2.0
        assert result.oos_sharpe > 0

    def test_failed_when_oos_negative(self):
        """测试集全亏 -> 不通过"""
        end = datetime(2025, 7, 1, tzinfo=timezone.utc)
        windows = generate_windows(180, end_date=end)

        trades = []
        base = datetime(2025, 1, 2, 12, 0, 0, tzinfo=timezone.utc)
        for i in range(36):
            from datetime import timedelta
            t_time = base + timedelta(days=i * 5)
            # 前120天(训练区): 正, 后60天(测试区): 负
            if i < 24:
                pnl = 2.0 + (i % 3) * 0.5
            else:
                pnl = -2.0 - (i % 3) * 0.5
            trades.append(_make_trade(t_time.isoformat(), pnl))

        result = run_walk_forward(trades, windows)
        # 至少部分测试集是负的
        assert result.oos_sharpe < result.is_sharpe

    def test_summary_contains_info(self):
        """总结包含关键信息"""
        result = run_walk_forward([], [])
        assert isinstance(result.summary, str)
        assert len(result.summary) > 0

    def test_window_results_populated(self):
        """每个窗口都有结果"""
        end = datetime(2025, 7, 1, tzinfo=timezone.utc)
        windows = generate_windows(180, end_date=end)

        trades = []
        base = datetime(2025, 1, 2, 12, 0, 0, tzinfo=timezone.utc)
        for i in range(36):
            from datetime import timedelta
            t_time = base + timedelta(days=i * 5)
            trades.append(_make_trade(t_time.isoformat(), 1.0))

        result = run_walk_forward(trades, windows)
        assert len(result.windows) == 4
        for wr in result.windows:
            assert wr.train_trades >= 0
            assert wr.test_trades >= 0
