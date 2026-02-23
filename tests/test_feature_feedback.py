"""Tests for ML feature importance feedback."""

from unittest.mock import MagicMock, patch


@patch("cryptobot.ml.feature_feedback.load_latest_model")
def test_no_model_returns_empty(mock_load):
    """无模型时返回空 dict"""
    mock_load.side_effect = FileNotFoundError("无可用模型")

    from cryptobot.ml.feature_feedback import build_feature_feedback_addon

    result = build_feature_feedback_addon()
    assert result == {}


@patch("cryptobot.ml.feature_feedback.load_latest_model")
def test_groups_by_role(mock_load):
    """按角色正确分组（使用 extractors.py 实际特征名）"""
    booster = MagicMock()
    booster.feature_name.return_value = [
        "rsi", "funding_rate", "fear_greed_index", "btc_correlation",
    ]
    booster.feature_importance.return_value = [100.0, 80.0, 60.0, 40.0]
    mock_load.return_value = (booster, "v1")

    from cryptobot.ml.feature_feedback import build_feature_feedback_addon

    result = build_feature_feedback_addon(top_n=4, epsilon=0.0)
    assert "technical" in result
    assert "onchain" in result
    assert "sentiment" in result
    assert "fundamental" in result


@patch("cryptobot.ml.feature_feedback.load_latest_model")
def test_addon_format(mock_load):
    """addon 文本格式正确"""
    booster = MagicMock()
    booster.feature_name.return_value = ["rsi", "macd_hist"]
    booster.feature_importance.return_value = [100.0, 50.0]
    mock_load.return_value = (booster, "v1")

    from cryptobot.ml.feature_feedback import build_feature_feedback_addon

    result = build_feature_feedback_addon(top_n=2, epsilon=0.0)
    addon = result["technical"]
    assert "### ML 特征重要性提示" in addon
    assert "rsi" in addon
    assert "macd_hist" in addon
    assert "100.0" in addon


@patch("cryptobot.ml.feature_feedback.load_latest_model")
def test_unmapped_defaults_to_technical(mock_load):
    """未映射的特征默认归 technical"""
    booster = MagicMock()
    booster.feature_name.return_value = ["unknown_feature_xyz"]
    booster.feature_importance.return_value = [99.0]
    mock_load.return_value = (booster, "v1")

    from cryptobot.ml.feature_feedback import build_feature_feedback_addon

    result = build_feature_feedback_addon(top_n=1, epsilon=0.0)
    assert "technical" in result
    assert "unknown_feature_xyz" in result["technical"]


@patch("cryptobot.ml.feature_feedback.load_latest_model")
def test_top_n_limits_features(mock_load):
    """top_n 参数正确限制特征数量"""
    booster = MagicMock()
    booster.feature_name.return_value = [
        "rsi", "macd_hist", "bb_position", "adx", "atr_pct",
    ]
    booster.feature_importance.return_value = [100.0, 80.0, 60.0, 40.0, 20.0]
    mock_load.return_value = (booster, "v1")

    from cryptobot.ml.feature_feedback import build_feature_feedback_addon

    result = build_feature_feedback_addon(top_n=2, epsilon=0.0)
    # 只取 top 2: rsi (100) + macd_hist (80), 都归 technical
    addon = result["technical"]
    assert "rsi" in addon
    assert "macd_hist" in addon
    assert "bb_position" not in addon
