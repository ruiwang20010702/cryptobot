"""分析师动态权重测试"""

from unittest.mock import patch

from cryptobot.journal.analyst_weights import (
    calc_analyst_weights,
    build_weights_context,
)


@patch("cryptobot.journal.analyst_weights.calc_analyst_accuracy")
def test_calc_weights_very_high(mock_accuracy):
    """测试卓越准确率 → very_high 权重"""
    mock_accuracy.return_value = {
        "technical": {"total": 15, "correct": 12, "accuracy": 0.80},
    }
    result = calc_analyst_weights()
    assert result["technical"]["weight"] == "very_high"
    assert "卓越" in result["technical"]["label"]


@patch("cryptobot.journal.analyst_weights.calc_analyst_accuracy")
def test_calc_weights_high(mock_accuracy):
    """测试高准确率 → high 权重"""
    mock_accuracy.return_value = {
        "technical": {"total": 15, "correct": 10, "accuracy": 0.67},
    }
    result = calc_analyst_weights()
    assert result["technical"]["weight"] == "high"
    assert result["technical"]["label"] != ""


@patch("cryptobot.journal.analyst_weights.calc_analyst_accuracy")
def test_calc_weights_very_low(mock_accuracy):
    """测试极低准确率 → very_low 权重"""
    mock_accuracy.return_value = {
        "sentiment": {"total": 12, "correct": 3, "accuracy": 0.25},
    }
    result = calc_analyst_weights()
    assert result["sentiment"]["weight"] == "very_low"
    assert "反向" in result["sentiment"]["label"]


@patch("cryptobot.journal.analyst_weights.calc_analyst_accuracy")
def test_calc_weights_low(mock_accuracy):
    """测试低准确率 → low 权重"""
    mock_accuracy.return_value = {
        "sentiment": {"total": 12, "correct": 5, "accuracy": 0.42},
    }
    result = calc_analyst_weights()
    assert result["sentiment"]["weight"] == "low"
    assert "仅供参考" in result["sentiment"]["label"]


@patch("cryptobot.journal.analyst_weights.calc_analyst_accuracy")
def test_calc_weights_normal(mock_accuracy):
    """测试普通准确率 → normal 权重"""
    mock_accuracy.return_value = {
        "onchain": {"total": 20, "correct": 11, "accuracy": 0.55},
    }
    result = calc_analyst_weights()
    assert result["onchain"]["weight"] == "normal"
    assert result["onchain"]["label"] == ""


@patch("cryptobot.journal.analyst_weights.calc_analyst_accuracy")
def test_calc_weights_insufficient_samples(mock_accuracy):
    """测试样本不足 → normal 权重（即使准确率很高/低）"""
    mock_accuracy.return_value = {
        "technical": {"total": 5, "correct": 5, "accuracy": 1.0},
        "sentiment": {"total": 3, "correct": 0, "accuracy": 0.0},
    }
    result = calc_analyst_weights()
    assert result["technical"]["weight"] == "normal"
    assert result["sentiment"]["weight"] == "normal"


@patch("cryptobot.journal.analyst_weights.calc_analyst_accuracy")
def test_build_weights_context_with_adjustments(mock_accuracy):
    """测试有调整时生成上下文文本"""
    mock_accuracy.return_value = {
        "technical": {"total": 20, "correct": 16, "accuracy": 0.80},
        "onchain": {"total": 15, "correct": 9, "accuracy": 0.60},
        "sentiment": {"total": 12, "correct": 4, "accuracy": 0.33},
        "fundamental": {"total": 18, "correct": 10, "accuracy": 0.56},
    }
    ctx = build_weights_context()
    assert "分析师权重" in ctx
    assert "technical" in ctx
    assert "sentiment" in ctx


@patch("cryptobot.journal.analyst_weights.calc_analyst_accuracy")
def test_build_weights_context_no_adjustments(mock_accuracy):
    """测试无调整时返回空字符串"""
    mock_accuracy.return_value = {
        "technical": {"total": 20, "correct": 12, "accuracy": 0.60},
        "onchain": {"total": 15, "correct": 9, "accuracy": 0.60},
    }
    ctx = build_weights_context()
    assert ctx == ""


@patch("cryptobot.journal.analyst_weights.calc_analyst_accuracy")
def test_build_weights_context_empty(mock_accuracy):
    """测试无数据时返回空字符串"""
    mock_accuracy.return_value = {}
    ctx = build_weights_context()
    assert ctx == ""
