"""币种差异化策略测试"""

import json
from datetime import datetime, timezone
from unittest.mock import patch

import pytest

from cryptobot.risk.symbol_profile import (
    SymbolGrade,
    _calc_grade,
    _grade_params,
    get_symbol_grade,
    grade_symbols,
    load_symbol_profiles,
)


class TestCalcGrade:
    def test_grade_a(self):
        assert _calc_grade(0.60, 3.0) == "A"

    def test_grade_b(self):
        assert _calc_grade(0.50, 1.0) == "B"

    def test_grade_c_by_win_rate(self):
        assert _calc_grade(0.40, -0.5) == "C"

    def test_grade_c_by_pnl(self):
        assert _calc_grade(0.30, -0.5) == "C"

    def test_grade_d(self):
        assert _calc_grade(0.20, -5.0) == "D"

    def test_boundary_a(self):
        # 刚好在 A 的边界
        assert _calc_grade(0.56, 2.1) == "A"
        # 不够 A
        assert _calc_grade(0.55, 2.0) != "A"


class TestGradeParams:
    def test_grade_a_params(self):
        lev, conf, blocked = _grade_params("A", 3)
        assert lev == 3
        assert conf == 0
        assert blocked is False

    def test_grade_b_params(self):
        lev, conf, blocked = _grade_params("B", 3)
        assert lev == 3
        assert conf == 5
        assert blocked is False

    def test_grade_c_params(self):
        lev, conf, blocked = _grade_params("C", 3)
        assert lev == 2
        assert conf == 10
        assert blocked is False

    def test_grade_d_params(self):
        lev, conf, blocked = _grade_params("D", 3)
        assert lev == 1
        assert conf == 0
        assert blocked is True

    def test_grade_c_min_leverage(self):
        lev, _, _ = _grade_params("C", 1)
        assert lev == 1  # 不低于 1


class _FakeRecord:
    def __init__(self, symbol, status, pnl_pct, timestamp):
        self.symbol = symbol
        self.status = status
        self.actual_pnl_pct = pnl_pct
        self.timestamp = timestamp


class TestGradeSymbols:
    @patch("cryptobot.journal.storage.get_all_records")
    @patch("cryptobot.config.get_pair_config")
    @patch("cryptobot.config.get_all_symbols")
    @patch("cryptobot.risk.symbol_profile._save_profiles")
    def test_grades_with_data(self, mock_save, mock_symbols, mock_pair, mock_records):
        mock_symbols.return_value = ["BTCUSDT"]
        mock_pair.return_value = {"default_leverage": 3}

        now = datetime.now(timezone.utc).isoformat()
        # 20 笔交易：12 赢 8 亏 → 胜率 60%, avg_pnl > 2%
        records = [
            _FakeRecord("BTCUSDT", "closed", 5.0, now)
            for _ in range(12)
        ] + [
            _FakeRecord("BTCUSDT", "closed", -2.0, now)
            for _ in range(8)
        ]
        mock_records.return_value = records

        result = grade_symbols(min_trades=15, days=180)
        assert len(result.grades) == 1
        assert result.grades[0].grade == "A"
        assert result.grades[0].win_rate == 0.6

    @patch("cryptobot.journal.storage.get_all_records")
    @patch("cryptobot.config.get_pair_config")
    @patch("cryptobot.config.get_all_symbols")
    @patch("cryptobot.risk.symbol_profile._save_profiles")
    def test_insufficient_data(self, mock_save, mock_symbols, mock_pair, mock_records):
        mock_symbols.return_value = ["DOGEUSDT"]
        mock_pair.return_value = {"default_leverage": 3}
        mock_records.return_value = []

        result = grade_symbols(min_trades=15, days=180)
        assert result.grades[0].grade == "C"
        assert result.grades[0].blocked is False


class TestLoadProfiles:
    def test_load_from_file(self, tmp_path):
        data = {
            "grades": [{
                "symbol": "BTCUSDT",
                "grade": "A",
                "win_rate": 0.6,
                "avg_pnl_pct": 3.0,
                "trade_count": 20,
                "recommended_leverage": 3,
                "min_confidence": 0,
                "blocked": False,
            }],
            "updated_at": "2026-01-01T00:00:00",
        }
        path = tmp_path / "symbol_profiles.json"
        path.write_text(json.dumps(data))

        with patch("cryptobot.risk.symbol_profile._PROFILES_PATH", path):
            profiles = load_symbol_profiles()
            assert "BTCUSDT" in profiles
            assert profiles["BTCUSDT"].grade == "A"

    def test_load_missing(self, tmp_path):
        path = tmp_path / "nonexistent.json"
        with patch("cryptobot.risk.symbol_profile._PROFILES_PATH", path):
            assert load_symbol_profiles() == {}

    def test_get_symbol_grade(self, tmp_path):
        data = {
            "grades": [{
                "symbol": "ETHUSDT",
                "grade": "B",
                "win_rate": 0.5,
                "avg_pnl_pct": 1.0,
                "trade_count": 20,
                "recommended_leverage": 3,
                "min_confidence": 5,
                "blocked": False,
            }],
        }
        path = tmp_path / "symbol_profiles.json"
        path.write_text(json.dumps(data))

        with patch("cryptobot.risk.symbol_profile._PROFILES_PATH", path):
            grade = get_symbol_grade("ETHUSDT")
            assert grade is not None
            assert grade.min_confidence == 5
            assert get_symbol_grade("NONEXIST") is None
