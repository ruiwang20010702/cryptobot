"""动态置信度阈值测试"""

from unittest.mock import patch

from cryptobot.journal.confidence_tuner import (
    calc_dynamic_threshold, build_threshold_context, _MIN_CLOSED,
)


def _mock_perf_insufficient():
    """样本不足的绩效数据"""
    return {
        "closed": 5,
        "confidence_calibration": {},
    }


def _mock_perf_optimistic():
    """偏乐观的绩效数据: 60-70 区间实际胜率远低于预期"""
    return {
        "closed": 30,
        "confidence_calibration": {
            "60-70": {"count": 10, "actual_win_rate": 0.35},  # 35% vs 期望 65% → 偏乐观
            "70-80": {"count": 8, "actual_win_rate": 0.70},   # 正常
            "80-90": {"count": 6, "actual_win_rate": 0.80},   # 正常
            "90+": {"count": 2, "actual_win_rate": None},
        },
    }


def _mock_perf_conservative():
    """偏保守的绩效数据: 高区间实际胜率远高于预期"""
    return {
        "closed": 25,
        "confidence_calibration": {
            "60-70": {"count": 5, "actual_win_rate": 0.60},   # 正常
            "70-80": {"count": 10, "actual_win_rate": 0.95},  # 95% vs 期望 75% → 偏保守
            "80-90": {"count": 6, "actual_win_rate": 0.95},   # 偏保守
            "90+": {"count": 1, "actual_win_rate": None},
        },
    }


def _mock_perf_normal():
    """校准正常的绩效数据"""
    return {
        "closed": 20,
        "confidence_calibration": {
            "60-70": {"count": 6, "actual_win_rate": 0.60},
            "70-80": {"count": 7, "actual_win_rate": 0.72},
            "80-90": {"count": 5, "actual_win_rate": 0.82},
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
