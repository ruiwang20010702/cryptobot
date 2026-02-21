"""Prompt Optimizer 单元测试"""

from datetime import datetime, timezone
from unittest.mock import patch

import pytest


@pytest.fixture
def opt_setup(tmp_path, monkeypatch):
    """设置临时目录"""
    import cryptobot.evolution.prompt_optimizer as po
    import cryptobot.evolution.prompt_manager as pm

    evo_dir = tmp_path / "evolution"
    evo_dir.mkdir()

    monkeypatch.setattr(po, "_ITERATIONS_DIR", evo_dir)
    monkeypatch.setattr(po, "_ITERATIONS_FILE", evo_dir / "iterations.json")
    monkeypatch.setattr(pm, "_VERSIONS_DIR", evo_dir)
    monkeypatch.setattr(pm, "_VERSIONS_FILE", evo_dir / "prompt_versions.json")

    return po


class TestCheckPerformanceDecline:
    def test_no_decline(self, opt_setup):
        po = opt_setup
        with patch("cryptobot.journal.analytics.calc_performance") as mock_perf:
            mock_perf.side_effect = [
                {"win_rate": 0.6, "closed": 10},  # 7d
                {"win_rate": 0.6, "closed": 30},  # 30d
            ]
            result = po.check_performance_decline()
            assert not result["declined"]

    def test_decline_detected(self, opt_setup):
        po = opt_setup
        with patch("cryptobot.journal.analytics.calc_performance") as mock_perf:
            mock_perf.side_effect = [
                {"win_rate": 0.3, "closed": 12},  # 7d: 30%
                {"win_rate": 0.6, "closed": 30},   # 30d: 60%
            ]
            result = po.check_performance_decline()
            assert result["declined"]
            assert result["gap_pct"] > 0

    def test_insufficient_samples(self, opt_setup):
        po = opt_setup
        with patch("cryptobot.journal.analytics.calc_performance") as mock_perf:
            mock_perf.side_effect = [
                {"win_rate": 0.2, "closed": 3},   # 7d: 不足 5 笔
                {"win_rate": 0.6, "closed": 30},
            ]
            result = po.check_performance_decline()
            assert not result["declined"]


class TestAnalyzeFailures:
    def test_no_losses(self, opt_setup):
        po = opt_setup
        with patch("cryptobot.journal.storage.get_all_records", return_value=[]):
            result = po.analyze_failures()
            assert "无亏损" in result

    def test_with_losses(self, opt_setup):
        po = opt_setup
        from cryptobot.journal.models import SignalRecord
        now = datetime.now(timezone.utc).isoformat()
        records = [
            SignalRecord(
                symbol="BTCUSDT", action="long", timestamp=now,
                confidence=60, actual_pnl_pct=-2.5, status="closed",
                reasoning="test reason", analyst_votes={"technical": "bullish"},
            ),
        ]
        with patch("cryptobot.journal.storage.get_all_records", return_value=records):
            result = po.analyze_failures()
            assert "BTCUSDT" in result
            assert "1 笔亏损" in result


class TestRunOptimizationCycle:
    def test_no_trigger(self, opt_setup):
        po = opt_setup
        with patch.object(po, "check_performance_decline") as mock_check:
            mock_check.return_value = {
                "declined": False, "win_rate_7d": 0.6, "win_rate_30d": 0.6,
                "gap_pct": 0, "closed_7d": 10, "closed_30d": 30,
            }
            result = po.run_optimization_cycle()
            assert not result["triggered"]

    def test_trigger_creates_version(self, opt_setup):
        po = opt_setup
        with (
            patch.object(po, "check_performance_decline") as mock_check,
            patch.object(po, "analyze_failures") as mock_analyze,
            patch.object(po, "generate_improved_prompt") as mock_gen,
        ):
            mock_check.return_value = {
                "declined": True, "win_rate_7d": 0.3, "win_rate_30d": 0.6,
                "gap_pct": 50.0, "closed_7d": 8, "closed_30d": 30,
            }
            mock_analyze.return_value = "测试失败分析"
            mock_gen.return_value = {
                "addons": {"TRADER": "改进提示"},
                "note": "测试改进",
            }

            result = po.run_optimization_cycle()
            assert result["triggered"]
            assert result["new_version"] == "v1.1"
