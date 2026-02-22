"""跨币种相关性风控模块测试"""

from dataclasses import FrozenInstanceError
from unittest.mock import patch

import pandas as pd
import pytest

from cryptobot.risk.correlation import (
    CorrelationMatrix,
    PortfolioRiskCheck,
    _mean,
    _pearson,
    _returns_from_closes,
    calc_correlation_matrix,
    calc_effective_positions,
    check_portfolio_correlation,
    get_correlation,
)


# ─── 辅助函数 ────────────────────────────────────────────────────────


def _make_klines_df(closes: list[float]) -> pd.DataFrame:
    """构造简易 K 线 DataFrame"""
    return pd.DataFrame({
        "open": closes,
        "high": closes,
        "low": closes,
        "close": closes,
        "volume": [100.0] * len(closes),
    })


def _mock_load_klines(data_map: dict[str, list[float]]):
    """返回一个 mock 函数，根据 symbol 返回对应 K 线"""
    def _loader(symbol: str, timeframe: str = "4h") -> pd.DataFrame:
        if symbol not in data_map:
            raise FileNotFoundError(f"No data for {symbol}")
        return _make_klines_df(data_map[symbol])
    return _loader


# ─── _pearson 基础测试 ───────────────────────────────────────────────


class TestPearson:
    def test_identical_series(self):
        xs = [1.0, 2.0, 3.0, 4.0, 5.0]
        assert _pearson(xs, xs) == pytest.approx(1.0)

    def test_opposite_series(self):
        xs = [1.0, 2.0, 3.0, 4.0, 5.0]
        ys = [5.0, 4.0, 3.0, 2.0, 1.0]
        assert _pearson(xs, ys) == pytest.approx(-1.0)

    def test_independent_series(self):
        # 正交向量 -> 相关性 = 0
        xs = [1.0, 0.0, -1.0, 0.0]
        ys = [0.0, 1.0, 0.0, -1.0]
        assert _pearson(xs, ys) == pytest.approx(0.0, abs=0.01)

    def test_short_series(self):
        """数据不足返回 0"""
        assert _pearson([1.0], [2.0]) == 0.0
        assert _pearson([], []) == 0.0

    def test_constant_series(self):
        """方差为 0 返回 0"""
        xs = [3.0, 3.0, 3.0]
        ys = [1.0, 2.0, 3.0]
        assert _pearson(xs, ys) == 0.0


class TestReturns:
    def test_basic(self):
        closes = [100.0, 110.0, 105.0]
        rets = _returns_from_closes(closes)
        assert len(rets) == 2
        assert rets[0] == pytest.approx(0.1)
        assert rets[1] == pytest.approx(-0.04545, abs=0.001)

    def test_empty(self):
        assert _returns_from_closes([]) == []
        assert _returns_from_closes([100.0]) == []


class TestMean:
    def test_basic(self):
        assert _mean([1.0, 2.0, 3.0]) == pytest.approx(2.0)

    def test_empty(self):
        assert _mean([]) == 0.0


# ─── calc_correlation_matrix ─────────────────────────────────────────


class TestCorrelationMatrix:
    @patch("cryptobot.risk.correlation._load_cache", return_value=None)
    @patch("cryptobot.risk.correlation._save_cache")
    @patch("cryptobot.risk.correlation._load_closes")
    def test_self_correlation(self, mock_closes, mock_save, mock_cache):
        """自相关 = 1.0"""
        mock_closes.return_value = [100, 105, 110, 108, 112]
        result = calc_correlation_matrix(["BTCUSDT"])
        assert result.matrix["BTCUSDT:BTCUSDT"] == 1.0

    @patch("cryptobot.risk.correlation._load_cache", return_value=None)
    @patch("cryptobot.risk.correlation._save_cache")
    @patch("cryptobot.risk.correlation._load_closes")
    def test_symmetry(self, mock_closes, mock_save, mock_cache):
        """对称性: corr(A,B) = corr(B,A)"""
        def side_effect(sym, tf, limit):
            data = {
                "BTCUSDT": [100, 105, 110, 108, 112],
                "ETHUSDT": [3000, 3100, 3200, 3150, 3250],
            }
            return data.get(sym, [])
        mock_closes.side_effect = side_effect

        result = calc_correlation_matrix(["BTCUSDT", "ETHUSDT"])
        # key 是按字母序排的，所以 "BTCUSDT:ETHUSDT"
        key = "BTCUSDT:ETHUSDT"
        assert key in result.matrix
        # 对称性通过 _make_key 保证
        assert get_correlation(result, "BTCUSDT", "ETHUSDT") == get_correlation(
            result, "ETHUSDT", "BTCUSDT"
        )

    @patch("cryptobot.risk.correlation._load_cache", return_value=None)
    @patch("cryptobot.risk.correlation._save_cache")
    @patch("cryptobot.risk.correlation._load_closes")
    def test_identical_prices_correlation_one(self, mock_closes, mock_save, mock_cache):
        """完全相同的价格序列 -> 相关性 = 1.0"""
        prices = [100.0, 105.0, 110.0, 108.0, 112.0, 115.0]
        mock_closes.return_value = prices

        result = calc_correlation_matrix(["AAA", "BBB"])
        key = "AAA:BBB"
        assert result.matrix[key] == pytest.approx(1.0, abs=0.01)

    @patch("cryptobot.risk.correlation._load_cache", return_value=None)
    @patch("cryptobot.risk.correlation._save_cache")
    @patch("cryptobot.risk.correlation._load_closes")
    def test_opposite_returns_correlation_neg_one(
        self, mock_closes, mock_save, mock_cache,
    ):
        """收益率完全相反 -> 相关性接近 -1.0"""
        def side_effect(sym, tf, limit):
            if sym == "AAA":
                # 交替涨跌: +10%, -5%, +8%, -3%, +6%
                return [100, 110, 104.5, 112.86, 109.47, 116.04]
            # 反向: -10%, +5%, -8%, +3%, -6%
            return [100, 90, 94.5, 86.94, 89.55, 84.18]
        mock_closes.side_effect = side_effect

        result = calc_correlation_matrix(["AAA", "BBB"])
        key = "AAA:BBB"
        assert result.matrix[key] == pytest.approx(-1.0, abs=0.05)

    @patch("cryptobot.risk.correlation._load_cache", return_value=None)
    @patch("cryptobot.risk.correlation._save_cache")
    @patch("cryptobot.risk.correlation._load_closes")
    def test_load_failure_fallback_zero(self, mock_closes, mock_save, mock_cache):
        """K 线加载失败 -> 相关性降级为 0.0"""
        def side_effect(sym, tf, limit):
            if sym == "AAA":
                return [100, 105, 110, 108, 112]
            return []  # 失败
        mock_closes.side_effect = side_effect

        result = calc_correlation_matrix(["AAA", "BBB"])
        key = "AAA:BBB"
        assert result.matrix[key] == 0.0

    @patch("cryptobot.risk.correlation._load_cache", return_value=None)
    @patch("cryptobot.risk.correlation._save_cache")
    @patch("cryptobot.risk.correlation._load_closes")
    def test_matrix_has_timestamp(self, mock_closes, mock_save, mock_cache):
        mock_closes.return_value = [100, 105, 110]
        result = calc_correlation_matrix(["BTCUSDT"])
        assert result.computed_at  # ISO timestamp 非空


# ─── check_portfolio_correlation ─────────────────────────────────────


class TestCheckPortfolioCorrelation:
    def _make_matrix(self, pairs: dict[str, float]) -> CorrelationMatrix:
        """快速构造矩阵，key 自动排序保证与 get_correlation 一致"""
        from cryptobot.risk.correlation import _make_key

        symbols = set()
        matrix = {}
        for key, val in pairs.items():
            a, b = key.split(":")
            symbols.add(a)
            symbols.add(b)
            matrix[_make_key(a, b)] = val
        # 补全自相关
        for s in symbols:
            matrix[_make_key(s, s)] = 1.0
        return CorrelationMatrix(
            symbols=sorted(symbols), matrix=matrix, computed_at="2024-01-01T00:00:00",
        )

    def test_high_corr_same_direction_violation(self):
        """高相关同向 3 个 -> violation"""
        matrix = self._make_matrix({
            "BTCUSDT:ETHUSDT": 0.85,
            "BTCUSDT:SOLUSDT": 0.80,
            "BTCUSDT:ADAUSDT": 0.75,
        })
        positions = [
            {"symbol": "ETHUSDT", "action": "long"},
            {"symbol": "SOLUSDT", "action": "long"},
            {"symbol": "ADAUSDT", "action": "long"},
        ]
        signal = {"symbol": "BTCUSDT", "action": "long"}
        result = check_portfolio_correlation(
            positions, signal, matrix, max_correlated_same_direction=3,
        )
        assert not result.passed
        assert len(result.violations) == 1

    def test_low_corr_passes(self):
        """低相关 -> passed"""
        matrix = self._make_matrix({
            "BTCUSDT:DOGEUSDT": 0.3,
        })
        positions = [
            {"symbol": "DOGEUSDT", "action": "long"},
        ]
        signal = {"symbol": "BTCUSDT", "action": "long"}
        result = check_portfolio_correlation(positions, signal, matrix)
        assert result.passed
        assert len(result.violations) == 0

    def test_opposite_direction_passes(self):
        """高相关但反向 -> passed"""
        matrix = self._make_matrix({
            "BTCUSDT:ETHUSDT": 0.95,
        })
        positions = [
            {"symbol": "ETHUSDT", "action": "short"},
        ]
        signal = {"symbol": "BTCUSDT", "action": "long"}
        result = check_portfolio_correlation(positions, signal, matrix)
        assert result.passed

    def test_empty_positions_passes(self):
        """空 positions -> passed"""
        matrix = self._make_matrix({})
        result = check_portfolio_correlation([], {"symbol": "BTCUSDT", "action": "long"}, matrix)
        assert result.passed
        assert result.effective_positions == 0.0

    def test_below_threshold_passes(self):
        """高相关但未达到数量上限 -> passed"""
        matrix = self._make_matrix({
            "BTCUSDT:ETHUSDT": 0.85,
            "BTCUSDT:SOLUSDT": 0.80,
        })
        positions = [
            {"symbol": "ETHUSDT", "action": "long"},
            {"symbol": "SOLUSDT", "action": "long"},
        ]
        signal = {"symbol": "BTCUSDT", "action": "long"}
        # 2 个同向高相关，阈值 3 -> 通过
        result = check_portfolio_correlation(
            positions, signal, matrix, max_correlated_same_direction=3,
        )
        assert result.passed


# ─── calc_effective_positions ────────────────────────────────────────


class TestEffectivePositions:
    def _make_matrix(self, pairs: dict[str, float]) -> CorrelationMatrix:
        from cryptobot.risk.correlation import _make_key

        symbols = set()
        matrix = {}
        for key, val in pairs.items():
            a, b = key.split(":")
            symbols.add(a)
            symbols.add(b)
            matrix[_make_key(a, b)] = val
        for s in symbols:
            matrix[_make_key(s, s)] = 1.0
        return CorrelationMatrix(
            symbols=sorted(symbols), matrix=matrix, computed_at="2024-01-01T00:00:00",
        )

    def test_single_position(self):
        """N=1 -> N_eff=1"""
        matrix = self._make_matrix({})
        matrix = CorrelationMatrix(
            symbols=["BTCUSDT"],
            matrix={"BTCUSDT:BTCUSDT": 1.0},
            computed_at="2024-01-01T00:00:00",
        )
        positions = [{"symbol": "BTCUSDT"}]
        assert calc_effective_positions(positions, matrix) == 1.0

    def test_two_fully_correlated(self):
        """N=2, corr=1.0 -> N_eff < 2"""
        matrix = self._make_matrix({
            "BTCUSDT:ETHUSDT": 1.0,
        })
        positions = [{"symbol": "BTCUSDT"}, {"symbol": "ETHUSDT"}]
        n_eff = calc_effective_positions(positions, matrix)
        # N_eff = 4/4 = 1.0
        assert n_eff == 1.0

    def test_two_uncorrelated(self):
        """N=2, corr=0.0 -> N_eff = 2"""
        matrix = self._make_matrix({
            "BTCUSDT:DOGEUSDT": 0.0,
        })
        positions = [{"symbol": "BTCUSDT"}, {"symbol": "DOGEUSDT"}]
        n_eff = calc_effective_positions(positions, matrix)
        # N_eff = 4/2 = 2.0
        assert n_eff == 2.0

    def test_empty_positions(self):
        """空 positions -> N_eff = 0"""
        matrix = CorrelationMatrix(
            symbols=[], matrix={}, computed_at="2024-01-01T00:00:00",
        )
        assert calc_effective_positions([], matrix) == 0.0


# ─── frozen dataclass ────────────────────────────────────────────────


class TestFrozen:
    def test_correlation_matrix_frozen(self):
        m = CorrelationMatrix(symbols=[], matrix={}, computed_at="")
        with pytest.raises(FrozenInstanceError):
            m.computed_at = "new"  # type: ignore[misc]

    def test_portfolio_risk_check_frozen(self):
        r = PortfolioRiskCheck(passed=True, violations=[], effective_positions=1.0)
        with pytest.raises(FrozenInstanceError):
            r.passed = False  # type: ignore[misc]
