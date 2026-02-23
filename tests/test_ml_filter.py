"""Tests for workflow/nodes/ml_filter.py"""

from unittest.mock import MagicMock, patch

from cryptobot.features.pipeline import FeatureVector
from cryptobot.ml.lgb_scorer import SignalScore
from cryptobot.workflow.nodes.ml_filter import ml_filter


def _make_decision(symbol="BTCUSDT", action="long", confidence=70):
    return {"symbol": symbol, "action": action, "confidence": confidence}


def _make_score(direction="up", probability=0.75, version="v1"):
    return SignalScore(
        symbol="BTCUSDT",
        direction=direction,
        probability=probability,
        model_version=version,
        features_used=10,
    )


def _make_fv(symbol="BTCUSDT"):
    return FeatureVector(
        symbol=symbol, timestamp="2026-01-01T00:00:00+00:00", features={"rsi": 45.0},
    )


def _state_with(decisions, market_data=None):
    return {
        "decisions": decisions,
        "market_data": market_data or {},
        "fear_greed": {},
        "stablecoin_flows": {},
        "dxy_data": {},
        "macro_events": {},
    }


# ─── 无模型全部放行 ───────────────────────────────────────────────────


@patch("cryptobot.workflow.nodes.ml_filter.ml_filter.__module__", "cryptobot.workflow.nodes.ml_filter")
@patch("cryptobot.ml.lgb_scorer.load_latest_model", side_effect=FileNotFoundError("no model"))
def test_no_model_passthrough(mock_load):
    """无模型时全部放行"""
    decs = [_make_decision(), _make_decision(action="short")]
    result = ml_filter(_state_with(decs))
    assert len(result["decisions"]) == 2
    assert result["decisions"][0]["action"] == "long"
    assert result["decisions"][1]["action"] == "short"


# ─── ML 方向一致 + 高概率 → 放行 ────────────────────────────────────


@patch("cryptobot.workflow.nodes.ml_filter._score_for_symbol")
@patch("cryptobot.ml.lgb_scorer.load_latest_model")
def test_same_direction_high_prob_pass(mock_load, mock_score):
    """ML 方向一致 + prob > 0.6 → 放行"""
    mock_load.return_value = (MagicMock(), "v1")
    mock_score.return_value = _make_score(direction="up", probability=0.75)

    dec = _make_decision(action="long", confidence=70)
    result = ml_filter(_state_with([dec]))

    d = result["decisions"][0]
    assert d["action"] == "long"
    assert d["confidence"] == 70  # 不变
    assert "ml_score" in d


# ─── ML 方向一致 + 低概率 → 降置信度 10 ─────────────────────────────


@patch("cryptobot.workflow.nodes.ml_filter._score_for_symbol")
@patch("cryptobot.ml.lgb_scorer.load_latest_model")
def test_same_direction_low_prob_reduce_confidence(mock_load, mock_score):
    """ML 方向一致 + prob 0.5-0.6 → confidence -= 10"""
    mock_load.return_value = (MagicMock(), "v1")
    # direction="up", prob=0.55 → dir_prob=0.55 (< 0.6)
    mock_score.return_value = _make_score(direction="up", probability=0.55)

    dec = _make_decision(action="long", confidence=70)
    result = ml_filter(_state_with([dec]))

    d = result["decisions"][0]
    assert d["action"] == "long"
    assert d["confidence"] == 60  # 70 - 10


# ─── ML 方向不一致 + 高概率 → 拒绝 ──────────────────────────────────


@patch("cryptobot.workflow.nodes.ml_filter._score_for_symbol")
@patch("cryptobot.ml.lgb_scorer.load_latest_model")
def test_opposite_direction_high_prob_reject(mock_load, mock_score):
    """ML 方向不一致 + prob > 0.65 → 拒绝"""
    mock_load.return_value = (MagicMock(), "v1")
    # action=long expects "up", ML says "down" with prob=0.3 → dir_prob for down = 1-0.3=0.7
    mock_score.return_value = _make_score(direction="down", probability=0.30)

    dec = _make_decision(action="long", confidence=70)
    result = ml_filter(_state_with([dec]))

    d = result["decisions"][0]
    assert d["action"] == "no_trade"
    assert d.get("ml_filtered") is True


# ─── ML 方向不一致 + 低概率 → 降置信度 15 ────────────────────────────


@patch("cryptobot.workflow.nodes.ml_filter._score_for_symbol")
@patch("cryptobot.ml.lgb_scorer.load_latest_model")
def test_opposite_direction_low_prob_reduce_confidence(mock_load, mock_score):
    """ML 方向不一致 + prob <= 0.65 → confidence -= 15"""
    mock_load.return_value = (MagicMock(), "v1")
    # action=long expects "up", ML says "down" with prob=0.45 → dir_prob for down = 1-0.45=0.55
    mock_score.return_value = _make_score(direction="down", probability=0.45)

    dec = _make_decision(action="long", confidence=70)
    result = ml_filter(_state_with([dec]))

    d = result["decisions"][0]
    assert d["action"] == "long"
    assert d["confidence"] == 55  # 70 - 15


# ─── no_trade 决策直接跳过 ───────────────────────────────────────────


@patch("cryptobot.workflow.nodes.ml_filter._score_for_symbol")
@patch("cryptobot.ml.lgb_scorer.load_latest_model")
def test_no_trade_skipped(mock_load, mock_score):
    """no_trade 决策不经过 ML 评分"""
    mock_load.return_value = (MagicMock(), "v1")

    dec = _make_decision(action="no_trade")
    result = ml_filter(_state_with([dec]))

    d = result["decisions"][0]
    assert d["action"] == "no_trade"
    assert "ml_score" not in d
    mock_score.assert_not_called()


# ─── 空 decisions ─────────────────────────────────────────────────────


def test_empty_decisions():
    """空 decisions → 空列表"""
    result = ml_filter({"decisions": []})
    assert result["decisions"] == []


# ─── short 方向测试 ──────────────────────────────────────────────────


@patch("cryptobot.workflow.nodes.ml_filter._score_for_symbol")
@patch("cryptobot.ml.lgb_scorer.load_latest_model")
def test_short_direction_match(mock_load, mock_score):
    """short 方向: ML says down + prob < 0.5 → dir_prob > 0.5 → 放行"""
    mock_load.return_value = (MagicMock(), "v1")
    # direction="down", prob=0.25 → dir_prob for down = 1-0.25=0.75
    mock_score.return_value = _make_score(direction="down", probability=0.25)

    dec = _make_decision(action="short", confidence=65)
    result = ml_filter(_state_with([dec]))

    d = result["decisions"][0]
    assert d["action"] == "short"
    assert d["confidence"] == 65  # 不变, 0.75 > 0.6


# ─── 不可变性检验 ─────────────────────────────────────────────────────


@patch("cryptobot.workflow.nodes.ml_filter._score_for_symbol")
@patch("cryptobot.ml.lgb_scorer.load_latest_model")
def test_immutability(mock_load, mock_score):
    """原始 decision dict 不被修改"""
    mock_load.return_value = (MagicMock(), "v1")
    mock_score.return_value = _make_score(direction="up", probability=0.75)

    original = _make_decision(action="long", confidence=70)
    original_copy = dict(original)
    ml_filter(_state_with([original]))

    assert original == original_copy
