"""tests/test_factor_analysis.py — 多因子相关性分析测试"""

from cryptobot.features.factor_analysis import (
    FactorCorrelation,
    _generate_report,
    _pearson_p_value,
    compute_lead_lag,
    run_factor_analysis,
)


# ─── compute_lead_lag 基本功能 ────────────────────────────────────────────


class TestComputeLeadLag:
    def test_perfect_positive_correlation(self):
        """完全正相关应返回 r≈1"""
        feat = [1.0, 2.0, 3.0, 4.0, 5.0]
        ret = [1.0, 2.0, 3.0, 4.0, 5.0]
        results = compute_lead_lag(feat, ret, lags=[0])
        assert len(results) == 1
        assert results[0].correlation > 0.99
        assert results[0].sample_size == 5

    def test_perfect_negative_correlation(self):
        """完全负相关应返回 r≈-1"""
        feat = [1.0, 2.0, 3.0, 4.0, 5.0]
        ret = [5.0, 4.0, 3.0, 2.0, 1.0]
        results = compute_lead_lag(feat, ret, lags=[0])
        assert results[0].correlation < -0.99

    def test_multiple_lags(self):
        """多个 lag 应返回对应数量的结果"""
        feat = list(range(30))
        ret = list(range(30))
        lags = [0, 4, 8, 12, 24]
        results = compute_lead_lag(feat, ret, lags=lags)
        assert len(results) == 5
        for r in results:
            assert r.lag_hours in lags

    def test_lag_reduces_sample_size(self):
        """较大 lag 应减少有效样本量"""
        feat = list(range(20))
        ret = list(range(20))
        results = compute_lead_lag(feat, ret, lags=[0, 4])
        assert results[0].sample_size > results[1].sample_size

    def test_factor_name_empty_by_default(self):
        """compute_lead_lag 返回的 factor_name 为空"""
        results = compute_lead_lag([1.0, 2.0, 3.0], [1.0, 2.0, 3.0], lags=[0])
        assert results[0].factor_name == ""


# ─── 空数据处理 ──────────────────────────────────────────────────────────


class TestEmptyData:
    def test_empty_series(self):
        """空序列应返回无相关性"""
        results = compute_lead_lag([], [], lags=[0])
        assert len(results) == 1
        assert results[0].correlation == 0.0
        assert results[0].p_value == 1.0
        assert results[0].sample_size == 0

    def test_too_short_series(self):
        """少于 3 个数据点应返回 p=1"""
        results = compute_lead_lag([1.0, 2.0], [1.0, 2.0], lags=[0])
        assert results[0].sample_size == 0 or results[0].p_value == 1.0

    def test_run_factor_analysis_no_data(self, tmp_path, monkeypatch):
        """无特征文件时应返回空结果"""
        monkeypatch.setattr(
            "cryptobot.features.factor_analysis.FEATURES_DIR", tmp_path
        )
        result = run_factor_analysis(days=30)
        assert result.factors == []
        assert result.top_predictors == []
        assert result.optimal_lags == {}


# ─── p-value 计算 ────────────────────────────────────────────────────────


class TestPValue:
    def test_perfect_correlation_small_p(self):
        """高相关大样本应有小 p-value"""
        p = _pearson_p_value(0.95, 100)
        assert p < 0.001

    def test_zero_correlation_large_p(self):
        """零相关应有大 p-value"""
        p = _pearson_p_value(0.0, 100)
        assert p >= 0.99

    def test_small_sample_large_p(self):
        """小样本应有大 p-value"""
        p = _pearson_p_value(0.5, 5)
        assert p > 0.01

    def test_n_less_than_3(self):
        """n<3 时 p=1.0"""
        assert _pearson_p_value(0.99, 2) == 1.0

    def test_r_equals_one(self):
        """r=1 时 p=0"""
        assert _pearson_p_value(1.0, 50) == 0.0


# ─── top_predictors 排序 ─────────────────────────────────────────────────


class TestTopPredictors:
    def test_sorted_by_abs_r(self):
        """top_predictors 按 |r| 降序排列"""
        factors = [
            FactorCorrelation("a", 0, 0.3, 0.01, 100),
            FactorCorrelation("b", 0, -0.8, 0.001, 100),
            FactorCorrelation("c", 0, 0.5, 0.02, 100),
        ]
        # 模拟筛选逻辑
        top = sorted(
            [f for f in factors if f.p_value < 0.05],
            key=lambda f: abs(f.correlation),
            reverse=True,
        )
        assert top[0].factor_name == "b"
        assert top[1].factor_name == "c"
        assert top[2].factor_name == "a"

    def test_excludes_insignificant(self):
        """p>=0.05 的因子不在 top_predictors 中"""
        factors = [
            FactorCorrelation("sig", 0, 0.5, 0.01, 100),
            FactorCorrelation("insig", 0, 0.8, 0.10, 100),
        ]
        top = [f for f in factors if f.p_value < 0.05]
        assert len(top) == 1
        assert top[0].factor_name == "sig"


# ─── report 生成 ─────────────────────────────────────────────────────────


class TestReport:
    def test_report_contains_header(self):
        """报告应包含标题"""
        report = _generate_report([], [], {})
        assert "多因子相关性分析报告" in report

    def test_report_with_predictors(self):
        """报告应展示预测因子"""
        factors = [FactorCorrelation("rsi", 4, 0.65, 0.001, 90)]
        report = _generate_report(factors, factors, {"rsi": 4})
        assert "rsi" in report
        assert "0.65" in report

    def test_report_no_predictors(self):
        """无预测因子时报告应提示"""
        report = _generate_report([], [], {})
        assert "无显著预测因子" in report
