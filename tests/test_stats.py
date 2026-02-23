"""统计检验测试"""

from dataclasses import dataclass

from cryptobot.backtest.stats import (
    ComparisonResult,
    _calc_sharpe,
    _welch_t_test,
    compare_with_baseline,
    run_permutation_test,
)


@dataclass
class FakeTrade:
    """模拟具有 net_pnl_pct 属性的交易对象"""

    net_pnl_pct: float


# ─── Welch's t-test ──────────────────────────────────────────────────────


class TestWelchTTest:
    def test_significant_difference(self):
        """均值差异明显 → p < 0.05"""
        g1 = [5, 6, 7, 8, 9] * 10  # mean=7
        g2 = [1, 2, 3, 4, 5] * 10  # mean=3
        p = _welch_t_test(g1, g2)
        assert p < 0.05

    def test_no_difference(self):
        """相同数据 → p > 0.05"""
        g = [5, 6, 7, 8, 9] * 10
        p = _welch_t_test(g, g.copy())
        assert p > 0.05

    def test_small_sample_returns_1(self):
        """样本不足 → p = 1.0"""
        assert _welch_t_test([1], [2, 3]) == 1.0
        assert _welch_t_test([], []) == 1.0
        assert _welch_t_test([1, 2], [3]) == 1.0

    def test_identical_values_zero_variance(self):
        """方差为零且均值相同 → p = 1.0"""
        p = _welch_t_test([5, 5, 5], [5, 5, 5])
        assert p == 1.0

    def test_zero_variance_different_means(self):
        """方差为零但均值不同 → p = 0.0"""
        p = _welch_t_test([5, 5, 5], [3, 3, 3])
        assert p == 0.0


# ─── Permutation test ────────────────────────────────────────────────────


class TestPermutationTest:
    def test_significant_difference(self):
        """均值差异明显 → p < 0.05"""
        g1 = [5, 6, 7, 8, 9] * 10
        g2 = [1, 2, 3, 4, 5] * 10
        p = run_permutation_test(g1, g2, n_permutations=5000, seed=42)
        assert p < 0.05

    def test_same_distribution(self):
        """相同分布 → p > 0.05"""
        g1 = [5, 6, 7, 8, 9] * 10
        g2 = [5, 6, 7, 8, 9] * 10
        p = run_permutation_test(g1, g2, n_permutations=5000, seed=42)
        assert p > 0.05

    def test_reproducible_with_seed(self):
        """固定 seed 结果可复现"""
        g1 = [3, 4, 5, 6, 7] * 5
        g2 = [1, 2, 3, 4, 5] * 5
        p1 = run_permutation_test(g1, g2, seed=123)
        p2 = run_permutation_test(g1, g2, seed=123)
        assert p1 == p2

    def test_empty_group_returns_1(self):
        """空组 → p = 1.0"""
        assert run_permutation_test([], [1, 2, 3]) == 1.0
        assert run_permutation_test([1, 2], []) == 1.0


# ─── Sharpe ratio ────────────────────────────────────────────────────────


class TestCalcSharpe:
    def test_positive_returns(self):
        """正收益 → Sharpe > 0"""
        returns = [1.0, 2.0, 3.0, 4.0, 5.0]
        sharpe = _calc_sharpe(returns)
        assert sharpe > 0

    def test_negative_returns(self):
        """负收益 → Sharpe < 0"""
        returns = [-5.0, -4.0, -3.0, -2.0, -1.0]
        sharpe = _calc_sharpe(returns)
        assert sharpe < 0

    def test_zero_std(self):
        """零标准差 → Sharpe = 0"""
        returns = [3.0, 3.0, 3.0]
        assert _calc_sharpe(returns) == 0.0

    def test_insufficient_data(self):
        """样本不足 → Sharpe = 0"""
        assert _calc_sharpe([]) == 0.0
        assert _calc_sharpe([5.0]) == 0.0

    def test_known_value(self):
        """验证计算公式: mean/std * sqrt(252) (统一年化因子)"""
        import math

        returns = [1.0, 2.0, 3.0, 4.0, 5.0]
        mean = 3.0
        var = sum((x - mean) ** 2 for x in returns) / 4  # n-1
        std = math.sqrt(var)
        expected = round(mean / std * math.sqrt(252), 4)
        assert _calc_sharpe(returns) == expected


# ─── compare_with_baseline ───────────────────────────────────────────────


class TestCompareWithBaseline:
    def test_significant_ai_better(self):
        """AI 明显优于基线"""
        ai = [FakeTrade(net_pnl_pct=x) for x in [5, 6, 7, 8, 9] * 10]
        bl = [FakeTrade(net_pnl_pct=x) for x in [1, 2, 3, 4, 5] * 10]
        result = compare_with_baseline(ai, bl, baseline_name="random")

        assert isinstance(result, ComparisonResult)
        assert result.significant is True
        assert result.pnl_p_value < 0.05
        assert result.ai_mean_pnl > result.baseline_mean_pnl
        assert result.n_ai == 50
        assert result.n_baseline == 50
        assert result.baseline_name == "random"

    def test_not_significant(self):
        """差异不显著: 两组数据均值几乎相同"""
        ai = [FakeTrade(net_pnl_pct=x) for x in [4, 5, 6, 7, 8] * 6]
        bl = [FakeTrade(net_pnl_pct=x) for x in [4.1, 5.1, 6.1, 6.9, 7.9] * 6]
        result = compare_with_baseline(ai, bl)

        assert result.pnl_p_value > 0.05
        assert result.significant is False

    def test_sharpe_populated(self):
        """Sharpe ratio 字段被正确填充"""
        ai = [FakeTrade(net_pnl_pct=x) for x in [2, 3, 4, 5, 6] * 5]
        bl = [FakeTrade(net_pnl_pct=x) for x in [-1, 0, 1, 2, 3] * 5]
        result = compare_with_baseline(ai, bl)

        assert result.ai_sharpe > 0
        assert result.ai_sharpe > result.baseline_sharpe
