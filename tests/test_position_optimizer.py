"""动态仓位优化测试

覆盖:
- calc_kelly_params 层级 fallback
- calc_portfolio_adjusted_size 相关性缩减
- calc_volatility_adjusted_leverage ATR 杠杆调整
- KellyParams frozen dataclass
"""

from dataclasses import FrozenInstanceError
from unittest.mock import patch

import pytest

from cryptobot.risk.position_sizer import (
    KellyParams,
    calc_kelly_params,
    calc_portfolio_adjusted_size,
    calc_volatility_adjusted_leverage,
)


# ─── 辅助 Mock 记录 ─────────────────────────────────────────────────


class FakeRecord:
    """轻量 mock，替代 SignalRecord"""

    def __init__(
        self, symbol="BTCUSDT", action="long",
        pnl=5.0, ts="2026-02-01T00:00:00+00:00",
    ):
        self.symbol = symbol
        self.action = action
        self.actual_pnl_pct = pnl
        self.status = "closed"
        self.timestamp = ts


def _make_records(n, symbol="BTCUSDT", action="long", pnl=5.0):
    return [
        FakeRecord(symbol=symbol, action=action, pnl=pnl)
        for _ in range(n)
    ]


# Mock 路径：在来源处 mock
STORAGE_MOCK = "cryptobot.journal.storage.get_all_records"
CORR_MOCK = "cryptobot.risk.correlation.get_correlation"


# ─── Kelly Fallback 链 ──────────────────────────────────────────────


class TestCalcKellyParams:
    def test_symbol_direction_source(self):
        """10+ 笔 BTC long -> source='journal'"""
        records = _make_records(12, "BTCUSDT", "long", 3.0)
        with patch(STORAGE_MOCK, return_value=records):
            kp = calc_kelly_params("BTCUSDT", "long")
        assert kp.source == "journal"
        assert kp.sample_size == 12
        assert kp.win_rate > 0

    def test_symbol_fallback(self):
        """币种+方向不足 10 笔 -> 退到 symbol (无方向区分)"""
        records = (
            _make_records(5, "BTCUSDT", "long", 3.0)
            + _make_records(7, "BTCUSDT", "short", 2.0)
        )
        with patch(STORAGE_MOCK, return_value=records):
            kp = calc_kelly_params("BTCUSDT", "long")
        assert kp.source == "journal"
        assert kp.sample_size == 12

    def test_direction_fallback(self):
        """币种不足 10 -> direction fallback (>= 15 笔同方向)"""
        records = (
            _make_records(5, "BTCUSDT", "long", 3.0)
            + _make_records(15, "ETHUSDT", "long", 2.0)
        )
        with patch(STORAGE_MOCK, return_value=records):
            kp = calc_kelly_params("BTCUSDT", "long")
        # BTCUSDT only 5 < 10, all long = 20 >= 15
        assert kp.source == "journal"

    def test_global_fallback(self):
        """方向不足 -> global (>= 20 笔)"""
        records = (
            _make_records(5, "BTCUSDT", "long", 3.0)
            + _make_records(5, "ETHUSDT", "short", -2.0)
            + _make_records(12, "SOLUSDT", "long", 1.0)
        )
        # action=None -> skip direction check, total 22 >= 20
        with patch(STORAGE_MOCK, return_value=records):
            kp = calc_kelly_params("BTCUSDT", action=None)
        assert kp.source == "journal"
        assert kp.sample_size == 22

    def test_default_fallback(self):
        """全部不足 -> default"""
        records = _make_records(3, "BTCUSDT", "long", 2.0)
        with patch(STORAGE_MOCK, return_value=records):
            kp = calc_kelly_params("BTCUSDT", "long")
        assert kp.source == "default"
        assert kp.win_rate == 0.5
        assert kp.avg_win_loss_ratio == 1.5

    def test_default_values(self):
        """默认 wr=0.5, ratio=1.5, kelly=0.5-(0.5/1.5)"""
        with patch(STORAGE_MOCK, return_value=[]):
            kp = calc_kelly_params("BTCUSDT")
        assert kp.win_rate == 0.5
        assert kp.avg_win_loss_ratio == 1.5
        expected_f = 0.5 - 0.5 / 1.5
        assert kp.kelly_fraction == pytest.approx(expected_f, abs=0.001)

    def test_confidence_high(self):
        """>= 30 笔 -> confidence_level='high'"""
        records = _make_records(35, "BTCUSDT", "long", 2.0)
        with patch(STORAGE_MOCK, return_value=records):
            kp = calc_kelly_params("BTCUSDT", "long")
        assert kp.confidence_level == "high"

    def test_confidence_medium(self):
        """15-29 笔 -> confidence_level='medium'"""
        records = _make_records(20, "BTCUSDT", "long", 2.0)
        with patch(STORAGE_MOCK, return_value=records):
            kp = calc_kelly_params("BTCUSDT", "long")
        assert kp.confidence_level == "medium"

    def test_confidence_low(self):
        """< 15 笔 -> confidence_level='low'"""
        records = _make_records(10, "BTCUSDT", "long", 2.0)
        with patch(STORAGE_MOCK, return_value=records):
            kp = calc_kelly_params("BTCUSDT", "long")
        assert kp.confidence_level == "low"

    def test_exception_returns_default(self):
        """get_all_records 异常 -> 返回 default"""
        with patch(STORAGE_MOCK, side_effect=RuntimeError("db error")):
            kp = calc_kelly_params("BTCUSDT")
        assert kp.source == "default"
        assert kp.win_rate == 0.5


# ─── Portfolio Adjusted Size ─────────────────────────────────────────


class FakeMatrix:
    """轻量 mock 相关性矩阵"""

    def __init__(self, correlations=None):
        self.matrix = correlations or {}


class TestPortfolioAdjustedSize:
    @patch(CORR_MOCK, return_value=0.85)
    def test_two_correlated_reduce_50pct(self, _mock):
        """2 个高相关 -> 缩减 50%"""
        positions = [
            {"symbol": "ETHUSDT", "action": "long"},
            {"symbol": "SOLUSDT", "action": "long"},
        ]
        result = calc_portfolio_adjusted_size(
            "BTCUSDT", 1000.0, positions, corr_matrix=FakeMatrix(),
        )
        assert result["adjusted_size_usdt"] == pytest.approx(500.0, abs=1)
        assert result["reduction_applied"] is True
        assert result["n_correlated"] == 2

    @patch(CORR_MOCK, return_value=0.9)
    def test_three_correlated_reduce_75pct(self, _mock):
        """3 个高相关 -> 缩减 75% (factor^2 = 0.25)"""
        positions = [
            {"symbol": "ETHUSDT", "action": "long"},
            {"symbol": "SOLUSDT", "action": "long"},
            {"symbol": "AVAXUSDT", "action": "long"},
        ]
        result = calc_portfolio_adjusted_size(
            "BTCUSDT", 1000.0, positions, corr_matrix=FakeMatrix(),
        )
        assert result["adjusted_size_usdt"] == pytest.approx(250.0, abs=1)
        assert result["reduction_applied"] is True
        assert result["n_correlated"] == 3

    @patch(CORR_MOCK, return_value=0.3)
    def test_no_correlated_no_reduction(self, _mock):
        """相关性低 -> 不缩减"""
        positions = [
            {"symbol": "DOGEUSDT", "action": "long"},
        ]
        result = calc_portfolio_adjusted_size(
            "BTCUSDT", 1000.0, positions, corr_matrix=FakeMatrix(),
        )
        assert result["adjusted_size_usdt"] == pytest.approx(1000.0, abs=1)
        assert result["reduction_applied"] is False
        assert result["n_correlated"] == 0

    def test_no_positions(self):
        """空持仓 -> 不缩减"""
        result = calc_portfolio_adjusted_size(
            "BTCUSDT", 1000.0, [], corr_matrix=FakeMatrix(),
        )
        assert result["adjusted_size_usdt"] == 1000.0
        assert result["reduction_applied"] is False

    def test_no_matrix(self):
        """无矩阵 -> 不缩减"""
        result = calc_portfolio_adjusted_size(
            "BTCUSDT", 1000.0,
            [{"symbol": "ETHUSDT", "action": "long"}],
            corr_matrix=None,
        )
        assert result["adjusted_size_usdt"] == 1000.0
        assert result["reduction_applied"] is False

    @patch(CORR_MOCK, return_value=0.9)
    def test_same_symbol_excluded(self, _mock):
        """同币种持仓不计入 n_correlated"""
        positions = [
            {"symbol": "BTCUSDT", "action": "long"},
            {"symbol": "ETHUSDT", "action": "long"},
        ]
        result = calc_portfolio_adjusted_size(
            "BTCUSDT", 1000.0, positions, corr_matrix=FakeMatrix(),
        )
        # BTCUSDT excluded, only ETHUSDT -> n=1 < 2 -> no reduction
        assert result["n_correlated"] == 1
        assert result["reduction_applied"] is False


# ─── Volatility Adjusted Leverage ────────────────────────────────────


class TestVolatilityAdjustedLeverage:
    def test_normal_no_change(self):
        """ATR 正常 -> 杠杆不变"""
        lev = calc_volatility_adjusted_leverage("BTCUSDT", 3, 2.0, 2.0)
        assert lev == 3

    def test_atr_1_5x_reduce_one(self):
        """ATR 1.5x+ -> 降一级"""
        lev = calc_volatility_adjusted_leverage("BTCUSDT", 3, 3.2, 2.0)
        assert lev == 2

    def test_atr_2x_reduce_two(self):
        """ATR 2.0x+ -> 降两级"""
        lev = calc_volatility_adjusted_leverage("BTCUSDT", 3, 4.5, 2.0)
        assert lev == 1

    def test_min_leverage_one(self):
        """杠杆不低于 1"""
        lev = calc_volatility_adjusted_leverage("BTCUSDT", 1, 5.0, 2.0)
        assert lev == 1

    def test_exactly_1_5x_no_reduce(self):
        """ATR 恰好 1.5x (不大于) -> 不降"""
        lev = calc_volatility_adjusted_leverage("BTCUSDT", 3, 3.0, 2.0)
        assert lev == 3

    def test_exactly_2x_reduce_one(self):
        """ATR 恰好 2.0x (不大于) -> 只降一级 (ratio=2.0 不 >2.0)"""
        lev = calc_volatility_adjusted_leverage("BTCUSDT", 3, 4.0, 2.0)
        assert lev == 2

    def test_zero_hist_atr(self):
        """hist_atr=0 -> 不降，返回 base"""
        lev = calc_volatility_adjusted_leverage("BTCUSDT", 3, 5.0, 0.0)
        assert lev == 3

    def test_high_base_leverage(self):
        """高基准杠杆也能正确降级"""
        lev = calc_volatility_adjusted_leverage("BTCUSDT", 5, 7.0, 3.0)
        # ratio=2.33 > 2.0 -> 降两级
        assert lev == 3


# ─── KellyParams Frozen ──────────────────────────────────────────────


class TestKellyParamsFrozen:
    def test_frozen(self):
        """KellyParams 不可修改"""
        kp = KellyParams(0.5, 1.5, 0.17, 10, "low", "default")
        with pytest.raises(FrozenInstanceError):
            kp.win_rate = 0.9  # type: ignore[misc]

    def test_fields(self):
        """所有字段正确初始化"""
        kp = KellyParams(0.6, 2.0, 0.35, 50, "high", "journal")
        assert kp.win_rate == 0.6
        assert kp.avg_win_loss_ratio == 2.0
        assert kp.kelly_fraction == 0.35
        assert kp.sample_size == 50
        assert kp.confidence_level == "high"
        assert kp.source == "journal"
