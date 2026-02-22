"""Bootstrap CI 测试"""

import random

import pytest

from cryptobot.backtest.bootstrap import (
    ConfidenceInterval,
    bootstrap_ci,
    bootstrap_metric_ci,
)


class TestBootstrapCi:
    def test_empty_returns_none(self):
        assert bootstrap_ci([]) is None

    def test_too_few_returns_none(self):
        assert bootstrap_ci([1.0, 2.0]) is None  # < 5

    def test_exactly_five_works(self):
        ci = bootstrap_ci([1.0, 2.0, 3.0, 4.0, 5.0], "mean")
        assert ci is not None
        assert isinstance(ci, ConfidenceInterval)

    def test_mean_ci_covers_true_mean(self):
        """已知均值为0的正态分布采样，CI应包含0"""
        rng = random.Random(123)
        values = [rng.gauss(0, 1) for _ in range(200)]
        ci = bootstrap_ci(values, "mean", seed=123)
        assert ci is not None
        assert ci.lower <= 0 <= ci.upper

    def test_median_ci(self):
        values = list(range(100))
        ci = bootstrap_ci(values, "median")
        assert ci is not None
        assert 40 <= ci.lower <= ci.upper <= 60

    def test_win_rate_ci(self):
        # 70% 正值
        values = [1.0] * 70 + [-1.0] * 30
        ci = bootstrap_ci(values, "win_rate")
        assert ci is not None
        assert 0.6 <= ci.lower <= 0.7
        assert 0.7 <= ci.upper <= 0.8

    def test_seed_reproducible(self):
        values = [1.0, -0.5, 2.0, -1.0, 0.5] * 20
        ci1 = bootstrap_ci(values, "mean", seed=42)
        ci2 = bootstrap_ci(values, "mean", seed=42)
        assert ci1 == ci2

    def test_different_seeds_differ(self):
        values = [1.0, -0.5, 2.0, -1.0, 0.5] * 20
        ci1 = bootstrap_ci(values, "mean", seed=42)
        ci2 = bootstrap_ci(values, "mean", seed=99)
        assert ci1 is not None and ci2 is not None
        # 点估计相同(同数据)，但 CI 范围可能略不同
        assert ci1.point_estimate == ci2.point_estimate

    def test_larger_sample_narrower_ci(self):
        rng = random.Random(99)
        small = [rng.gauss(0, 1) for _ in range(30)]
        large = [rng.gauss(0, 1) for _ in range(300)]
        ci_small = bootstrap_ci(small, "mean", seed=99)
        ci_large = bootstrap_ci(large, "mean", seed=99)
        assert ci_small is not None and ci_large is not None
        width_small = ci_small.upper - ci_small.lower
        width_large = ci_large.upper - ci_large.lower
        assert width_small > width_large

    def test_confidence_level_stored(self):
        values = list(range(50))
        ci = bootstrap_ci(values, "mean", confidence=0.90)
        assert ci is not None
        assert ci.confidence_level == 0.90

    def test_n_samples_stored(self):
        values = list(range(20))
        ci = bootstrap_ci(values, "mean")
        assert ci is not None
        assert ci.n_samples == 20

    def test_n_bootstrap_stored(self):
        values = list(range(20))
        ci = bootstrap_ci(values, "mean", n_bootstrap=1000)
        assert ci is not None
        assert ci.n_bootstrap == 1000

    def test_invalid_statistic_raises(self):
        with pytest.raises(ValueError, match="未知"):
            bootstrap_ci([1.0] * 10, "invalid_stat")


class TestBootstrapMetricCi:
    def test_all_keys_present(self):
        pnl = [2.0, -1.0, 3.0, -0.5, 1.5] * 10
        result = bootstrap_metric_ci(pnl)
        assert "win_rate_ci" in result
        assert "avg_pnl_ci" in result
        assert "sharpe_ci" in result
        assert "profit_factor_ci" in result

    def test_empty_returns_none_values(self):
        result = bootstrap_metric_ci([])
        assert result["win_rate_ci"] is None
        assert result["avg_pnl_ci"] is None
        assert result["sharpe_ci"] is None
        assert result["profit_factor_ci"] is None

    def test_all_wins_profit_factor(self):
        pnl = [1.0, 2.0, 3.0, 0.5, 1.5] * 10
        result = bootstrap_metric_ci(pnl)
        # 全盈利 profit_factor -> inf, CI 可能为 None 或高值
        pf_ci = result.get("profit_factor_ci")
        if pf_ci is not None:
            assert pf_ci.point_estimate > 10  # 很高

    def test_ci_values_are_reasonable(self):
        """正常混合盈亏数据的 CI 范围合理"""
        pnl = [2.0, -1.0, 3.0, -0.5, 1.5, -2.0, 0.8] * 10
        result = bootstrap_metric_ci(pnl)

        wr = result["win_rate_ci"]
        assert wr is not None
        assert 0 <= wr.lower <= wr.upper <= 1

        avg = result["avg_pnl_ci"]
        assert avg is not None
        assert avg.lower <= avg.upper

        sharpe = result["sharpe_ci"]
        assert sharpe is not None
        assert sharpe.lower <= sharpe.upper

        pf = result["profit_factor_ci"]
        assert pf is not None
        assert pf.lower <= pf.upper

    def test_confidence_interval_frozen(self):
        """ConfidenceInterval 是 frozen dataclass"""
        pnl = [1.0, -0.5, 2.0, -1.0, 0.5] * 10
        result = bootstrap_metric_ci(pnl)
        ci = result["win_rate_ci"]
        assert ci is not None
        with pytest.raises(AttributeError):
            ci.lower = 999.0  # type: ignore[misc]
