"""Model Competition 单元测试"""

from unittest.mock import patch

import pytest


@pytest.fixture
def comp_setup(tmp_path, monkeypatch):
    """设置临时目录"""
    import cryptobot.evolution.model_competition as mc

    evo_dir = tmp_path / "evolution"
    evo_dir.mkdir()
    monkeypatch.setattr(mc, "_COMP_DIR", evo_dir)
    monkeypatch.setattr(mc, "_COMP_FILE", evo_dir / "competition.json")
    return mc


class TestGetCompetitionConfig:
    def test_disabled(self, comp_setup):
        mc = comp_setup
        with patch("cryptobot.evolution.model_competition.load_settings", return_value={
            "llm": {"competition": {"enabled": False}},
        }):
            assert mc.get_competition_config() is None

    def test_enabled(self, comp_setup):
        mc = comp_setup
        with patch("cryptobot.evolution.model_competition.load_settings", return_value={
            "llm": {"competition": {
                "enabled": True,
                "models": [
                    {"id": "model-a", "label": "Model A"},
                    {"id": "model-b", "label": "Model B"},
                ],
                "strategy": "consensus",
            }},
        }):
            cfg = mc.get_competition_config()
            assert cfg is not None
            assert len(cfg["models"]) == 2

    def test_insufficient_models(self, comp_setup):
        mc = comp_setup
        with patch("cryptobot.evolution.model_competition.load_settings", return_value={
            "llm": {"competition": {
                "enabled": True,
                "models": [{"id": "only-one"}],
            }},
        }):
            assert mc.get_competition_config() is None


class TestSelectWinner:
    def test_consensus_agreement(self, comp_setup):
        mc = comp_setup
        results = [
            {"model_id": "a", "result": {"action": "long", "confidence": 70, "reasoning": "ok"}},
            {"model_id": "b", "result": {"action": "long", "confidence": 80, "reasoning": "ok"}},
        ]
        winner = mc.select_winner(results, "consensus", "BTCUSDT")
        assert winner["result"]["action"] == "long"
        assert winner["model_id"] == "b"  # 更高置信度

    def test_consensus_disagreement(self, comp_setup):
        mc = comp_setup
        results = [
            {"model_id": "a", "result": {"action": "long", "confidence": 70, "reasoning": "ok"}},
            {"model_id": "b", "result": {"action": "short", "confidence": 80, "reasoning": "ok"}},
        ]
        winner = mc.select_winner(results, "consensus", "BTCUSDT")
        assert winner["result"]["action"] == "no_trade"

    def test_consensus_three_models(self, comp_setup):
        mc = comp_setup
        results = [
            {"model_id": "a", "result": {"action": "long", "confidence": 70, "reasoning": "ok"}},
            {"model_id": "b", "result": {"action": "long", "confidence": 60, "reasoning": "ok"}},
            {"model_id": "c", "result": {"action": "short", "confidence": 80, "reasoning": "ok"}},
        ]
        winner = mc.select_winner(results, "consensus", "BTCUSDT")
        assert winner["result"]["action"] == "long"  # 2/3 同意

    def test_all_errors(self, comp_setup):
        mc = comp_setup
        results = [
            {"model_id": "a", "result": {"error": "fail"}},
            {"model_id": "b", "result": {"error": "fail"}},
        ]
        winner = mc.select_winner(results, "consensus", "BTCUSDT")
        assert winner["result"]["action"] == "no_trade"

    def test_best_performer(self, comp_setup):
        mc = comp_setup
        results = [
            {"model_id": "a", "result": {"action": "long", "confidence": 70, "reasoning": "ok"}},
            {"model_id": "b", "result": {"action": "short", "confidence": 80, "reasoning": "ok"}},
        ]
        with patch.object(mc, "get_model_stats", return_value={
            "a": {"total": 20, "wins": 14, "win_rate": 0.7},
            "b": {"total": 20, "wins": 10, "win_rate": 0.5},
        }):
            winner = mc.select_winner(results, "best_performer", "BTCUSDT")
            assert winner["model_id"] == "a"


class TestRecordAndStats:
    def test_record_competition(self, comp_setup):
        mc = comp_setup
        mc.record_competition_result("BTCUSDT", "model-a", "long", "sig123")
        data = mc._load_competition()
        assert len(data) == 1
        assert data[0]["symbol"] == "BTCUSDT"

    def test_get_model_stats_empty(self, comp_setup):
        mc = comp_setup
        assert mc.get_model_stats() == {}

    def test_record_limit(self, comp_setup):
        mc = comp_setup
        for i in range(510):
            mc.record_competition_result("BTCUSDT", "a", "long", f"sig{i}")
        data = mc._load_competition()
        assert len(data) == 500
