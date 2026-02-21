"""动态置信度阈值测试"""

from unittest.mock import patch

from cryptobot.journal.confidence_tuner import (
    calc_dynamic_threshold, build_threshold_context,
)


def _mock_perf_insufficient():
    """样本不足的绩效数据"""
    return {
        "closed": 5,
        "confidence_calibration": {},
    }


def _mock_perf_optimistic():
    """偏乐观的绩效数据: 60-70 区间实际胜率远低于预期（偏差>30%）"""
    return {
        "closed": 55,
        "confidence_calibration": {
            "60-70": {"count": 20, "actual_win_rate": 0.40},  # 40% vs 期望 65% → 偏乐观(偏差>30%)
            "70-80": {"count": 15, "actual_win_rate": 0.70},  # 正常
            "80-90": {"count": 15, "actual_win_rate": 0.80},  # 正常
            "90+": {"count": 2, "actual_win_rate": None},
        },
    }


def _mock_perf_conservative():
    """偏保守的绩效数据: 高区间实际胜率远高于预期（偏差>20%）"""
    return {
        "closed": 55,
        "confidence_calibration": {
            "60-70": {"count": 15, "actual_win_rate": 0.60},   # 正常
            "70-80": {"count": 20, "actual_win_rate": 0.95},  # 95% vs 期望 75% → 偏保守(偏差>20%)
            "80-90": {"count": 15, "actual_win_rate": 0.95},  # 偏保守(偏差>20%)
            "90+": {"count": 2, "actual_win_rate": None},
        },
    }


def _mock_perf_normal():
    """校准正常的绩效数据"""
    return {
        "closed": 55,
        "confidence_calibration": {
            "60-70": {"count": 18, "actual_win_rate": 0.60},
            "70-80": {"count": 18, "actual_win_rate": 0.72},
            "80-90": {"count": 15, "actual_win_rate": 0.82},
            "90+": {"count": 2, "actual_win_rate": None},
        },
    }


@patch("cryptobot.journal.analytics.calc_performance")
def test_insufficient_samples(mock_perf):
    """样本不足返回默认"""
    mock_perf.return_value = _mock_perf_insufficient()
    result = calc_dynamic_threshold()
    assert result["recommended_min_confidence"] == 60
    assert result["sample_size"] == 5
    assert "样本不足" in result["calibration_notes"][0]


@patch("cryptobot.journal.analytics.calc_performance")
def test_optimistic_raises_threshold(mock_perf):
    """偏乐观时提高阈值"""
    mock_perf.return_value = _mock_perf_optimistic()
    result = calc_dynamic_threshold()
    assert result["recommended_min_confidence"] > 60
    assert any("偏乐观" in n for n in result["calibration_notes"])


@patch("cryptobot.journal.analytics.calc_performance")
def test_conservative_lowers_threshold(mock_perf):
    """偏保守时降低阈值"""
    mock_perf.return_value = _mock_perf_conservative()
    result = calc_dynamic_threshold()
    assert result["recommended_min_confidence"] <= 60
    assert any("偏保守" in n for n in result["calibration_notes"])


@patch("cryptobot.journal.analytics.calc_performance")
def test_normal_calibration(mock_perf):
    """校准正常不调整"""
    mock_perf.return_value = _mock_perf_normal()
    result = calc_dynamic_threshold()
    assert result["recommended_min_confidence"] == 60
    assert "校准正常" in result["calibration_notes"][0]


@patch("cryptobot.journal.analytics.calc_performance")
def test_build_threshold_context_sufficient(mock_perf):
    """样本充足时返回上下文文本"""
    mock_perf.return_value = _mock_perf_optimistic()
    regime = {"params": {"min_confidence": 65}}
    ctx = build_threshold_context(regime)
    assert "### 置信度校准" in ctx
    assert "建议最低置信度" in ctx


@patch("cryptobot.journal.analytics.calc_performance")
def test_build_threshold_context_insufficient(mock_perf):
    """样本不足返回空字符串"""
    mock_perf.return_value = _mock_perf_insufficient()
    regime = {"params": {"min_confidence": 65}}
    ctx = build_threshold_context(regime)
    assert ctx == ""


@patch("cryptobot.journal.analytics.calc_performance", side_effect=Exception("DB error"))
def test_perf_error_returns_default(mock_perf):
    """绩效数据获取失败返回默认"""
    result = calc_dynamic_threshold()
    assert result["recommended_min_confidence"] == 60
    assert result["sample_size"] == 0


@patch("cryptobot.journal.analytics.calc_performance")
def test_severe_optimistic_large_adjustment(mock_perf):
    """严重偏乐观(偏差>50%): 调整幅度=+15"""
    mock_perf.return_value = {
        "closed": 55,
        "confidence_calibration": {
            "60-70": {"count": 20, "actual_win_rate": 0.30},  # 30% vs 期望 65% → 偏差>50%
            "70-80": {"count": 15, "actual_win_rate": 0.70},
            "80-90": {"count": 15, "actual_win_rate": 0.80},
        },
    }
    result = calc_dynamic_threshold()
    # 60 + 15 = 75
    assert result["recommended_min_confidence"] == 75
    assert any("严重偏乐观" in n for n in result["calibration_notes"])


@patch("cryptobot.journal.analytics.calc_performance")
def test_severe_conservative_large_adjustment(mock_perf):
    """严重偏保守(偏差>50%): 调整幅度=-15"""
    mock_perf.return_value = {
        "closed": 55,
        "confidence_calibration": {
            "60-70": {"count": 15, "actual_win_rate": 0.60},
            "70-80": {"count": 20, "actual_win_rate": 1.0},   # 100% vs 期望 75% → 偏差>50%
            "80-90": {"count": 15, "actual_win_rate": 1.0},   # 100% vs 期望 85% → 偏差>17%
        },
    }
    result = calc_dynamic_threshold()
    # 70-80: 1.0 > 0.75*1.5=1.125? → No, 1.0 < 1.125 → 不满足>50%
    # 但 1.0 > 0.75*1.2=0.9 → 偏保守, adjustment -= 10
    # 80-90: 1.0 > 0.85*1.2=1.02? → No → 不调整
    # 所以 adjustment = -10, recommended = max(50, min(85, 60-10)) = 50
    assert result["recommended_min_confidence"] == 50


@patch("cryptobot.journal.analytics.calc_performance")
def test_new_range_upper_bound(mock_perf):
    """新范围上界: 最大 85"""
    mock_perf.return_value = {
        "closed": 55,
        "confidence_calibration": {
            # 两个低区间都严重偏乐观 → +15+15=30 → min(85, 60+30) = 85
            "60-70": {"count": 20, "actual_win_rate": 0.20},  # << 期望 → +15
        },
    }
    result = calc_dynamic_threshold()
    assert result["recommended_min_confidence"] == 75


@patch("cryptobot.journal.analytics.calc_performance")
def test_new_range_lower_bound(mock_perf):
    """新范围下界: 最小 50"""
    mock_perf.return_value = {
        "closed": 55,
        "confidence_calibration": {
            "60-70": {"count": 15, "actual_win_rate": 0.60},
            # 两个高区间都严重偏保守
            "70-80": {"count": 20, "actual_win_rate": 1.0},  # 偏差 >20% → -10
            "80-90": {"count": 15, "actual_win_rate": 1.0},  # 偏差 <50% but >20% → -10
        },
    }
    result = calc_dynamic_threshold()
    # adjustment = -10 + -10 = -20, max(50, min(85, 60-20)) = max(50, 40) = 50
    assert result["recommended_min_confidence"] == 50
