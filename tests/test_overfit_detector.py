"""过拟合检测器测试"""

import json
from datetime import datetime, timezone
from unittest.mock import patch

import pytest

from cryptobot.evolution.overfit_detector import (
    OverfitReport,
    _count_recent_modifications,
    _load_json_safe,
    detect_overfit,
)


class TestLoadJsonSafe:
    def test_missing_file(self, tmp_path):
        result = _load_json_safe(tmp_path / "nonexistent.json")
        assert result == []

    def test_valid_json_list(self, tmp_path):
        f = tmp_path / "test.json"
        f.write_text('[{"a": 1}]')
        result = _load_json_safe(f)
        assert result == [{"a": 1}]

    def test_valid_json_dict(self, tmp_path):
        f = tmp_path / "test.json"
        f.write_text('{"k": "v"}')
        result = _load_json_safe(f)
        assert result == {"k": "v"}

    def test_invalid_json(self, tmp_path):
        f = tmp_path / "bad.json"
        f.write_text("not json")
        result = _load_json_safe(f)
        assert result == []

    def test_non_collection_json(self, tmp_path):
        f = tmp_path / "str.json"
        f.write_text('"just a string"')
        result = _load_json_safe(f)
        assert result == []


class TestCountRecentModifications:
    def test_empty_list(self):
        assert _count_recent_modifications([], 7) == 0

    def test_counts_recent(self):
        now = datetime.now(timezone.utc).isoformat()
        data = [
            {"timestamp": now},
            {"timestamp": "2020-01-01T00:00:00+00:00"},
        ]
        assert _count_recent_modifications(data, 7) == 1

    def test_dict_values(self):
        now = datetime.now(timezone.utc).isoformat()
        data = {
            "a": {"created_at": now},
            "b": {"created_at": "2020-01-01T00:00:00+00:00"},
        }
        assert _count_recent_modifications(
            data, 7, "created_at",
        ) == 1

    def test_skips_non_dict_items(self):
        result = _count_recent_modifications(
            ["not_a_dict", 123], 7,
        )
        assert result == 0


class TestDetectOverfit:
    @patch(
        "cryptobot.evolution.overfit_detector._EVOLUTION_DIR",
    )
    @patch(
        "cryptobot.evolution.overfit_detector"
        "._calc_performance_trend",
    )
    def test_empty_records_score_zero(
        self, mock_perf, mock_dir, tmp_path,
    ):
        """空记录 -> score=0"""
        mock_dir.__truediv__ = lambda self, x: tmp_path / x
        mock_perf.return_value = {
            "improved": True,
            "full_period": {},
            "recent_half": {},
        }

        report = detect_overfit(30)
        assert report.overfit_score == 0
        assert len(report.signals) == 0
        assert "未检测到" in report.recommendation

    @patch(
        "cryptobot.evolution.overfit_detector._EVOLUTION_DIR",
    )
    @patch(
        "cryptobot.evolution.overfit_detector"
        "._calc_performance_trend",
    )
    def test_high_frequency_degraded_high_score(
        self, mock_perf, mock_dir, tmp_path,
    ):
        """高频修改 + 绩效下降 -> score > 70"""
        mock_dir.__truediv__ = lambda self, x: tmp_path / x
        mock_perf.return_value = {
            "improved": False,
            "full_period": {},
            "recent_half": {},
        }

        now = datetime.now(timezone.utc).isoformat()
        iters = [{"timestamp": now} for _ in range(5)]
        (tmp_path / "iterations.json").write_text(
            json.dumps(iters),
        )
        rules = [{"created_at": now} for _ in range(3)]
        (tmp_path / "strategy_rules.json").write_text(
            json.dumps(rules),
        )
        (tmp_path / "prompt_versions.json").write_text("[]")

        report = detect_overfit(30)
        assert report.overfit_score > 70
        assert len(report.signals) > 0
        assert "暂停" in report.recommendation

    @patch(
        "cryptobot.evolution.overfit_detector._EVOLUTION_DIR",
    )
    @patch(
        "cryptobot.evolution.overfit_detector"
        "._calc_performance_trend",
    )
    def test_occasional_modification_improved_low_score(
        self, mock_perf, mock_dir, tmp_path,
    ):
        """偶尔修改 + 改善 -> score < 30"""
        mock_dir.__truediv__ = lambda self, x: tmp_path / x
        mock_perf.return_value = {
            "improved": True,
            "full_period": {},
            "recent_half": {},
        }

        now = datetime.now(timezone.utc).isoformat()
        (tmp_path / "iterations.json").write_text(
            json.dumps([{"timestamp": now}]),
        )
        (tmp_path / "strategy_rules.json").write_text("[]")
        (tmp_path / "prompt_versions.json").write_text("[]")

        report = detect_overfit(30)
        assert report.overfit_score < 30

    def test_report_is_frozen_dataclass(self):
        """确保 OverfitReport 不可变"""
        report = OverfitReport(
            modification_frequency={},
            performance_trend={},
            overfit_score=0,
            signals=[],
            recommendation="ok",
        )
        with pytest.raises(AttributeError):
            report.overfit_score = 50  # type: ignore[misc]
