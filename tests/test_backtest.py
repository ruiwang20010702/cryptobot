"""回测评估测试"""

import json
from unittest.mock import patch, MagicMock

import pytest
from click.testing import CliRunner

from cryptobot.backtest.evaluator import (
    evaluate_signals,
    replay_signal,
    _group_stats,
    _calc_risk_reward,
    _calc_streak,
)
from cryptobot.journal.models import SignalRecord
from cryptobot.cli.backtest import backtest


def _make_record(**overrides) -> SignalRecord:
    base = {
        "symbol": "BTCUSDT",
        "action": "long",
        "timestamp": "2026-02-15T00:00:00+00:00",
        "confidence": 75,
        "leverage": 3,
        "status": "closed",
        "actual_pnl_pct": 5.0,
        "actual_pnl_usdt": 150.0,
        "entry_price_range": [94000, 96000],
        "stop_loss": 91000,
        "take_profit": [{"price": 100000, "close_pct": 50}],
    }
    base.update(overrides)
    return SignalRecord.from_dict(base)


# ─── evaluate_signals ─────────────────────────────────────────────────────

class TestEvaluateSignals:
    @patch("cryptobot.backtest.evaluator.get_all_records")
    def test_empty_records(self, mock_get):
        mock_get.return_value = []
        result = evaluate_signals(30)
        assert result["overview"]["total"] == 0

    @patch("cryptobot.backtest.evaluator.get_all_records")
    def test_with_closed_records(self, mock_get):
        mock_get.return_value = [
            _make_record(symbol="BTCUSDT", actual_pnl_pct=5.0, actual_pnl_usdt=150),
            _make_record(symbol="BTCUSDT", actual_pnl_pct=-2.0, actual_pnl_usdt=-60),
            _make_record(symbol="ETHUSDT", actual_pnl_pct=3.0, actual_pnl_usdt=90),
        ]
        result = evaluate_signals(30)

        overview = result["overview"]
        assert overview["total"] == 3
        assert overview["wins"] == 2
        assert overview["losses"] == 1
        assert overview["win_rate"] == pytest.approx(0.667, abs=0.01)
        assert overview["best_trade_pct"] == 5.0
        assert overview["worst_trade_pct"] == -2.0

    @patch("cryptobot.backtest.evaluator.get_all_records")
    def test_by_symbol(self, mock_get):
        mock_get.return_value = [
            _make_record(symbol="BTCUSDT", actual_pnl_pct=5.0, actual_pnl_usdt=150),
            _make_record(symbol="ETHUSDT", actual_pnl_pct=-2.0, actual_pnl_usdt=-60),
        ]
        result = evaluate_signals(30)

        by_symbol = result["by_symbol"]
        assert "BTCUSDT" in by_symbol
        assert "ETHUSDT" in by_symbol
        assert by_symbol["BTCUSDT"]["win_rate"] == 1.0
        assert by_symbol["ETHUSDT"]["win_rate"] == 0.0

    @patch("cryptobot.backtest.evaluator.get_all_records")
    def test_old_records_excluded(self, mock_get):
        """超出天数范围的记录不计入"""
        mock_get.return_value = [
            _make_record(timestamp="2025-01-01T00:00:00+00:00", actual_pnl_pct=5.0),
        ]
        result = evaluate_signals(30)
        assert result["overview"]["total"] == 0


# ─── _group_stats ─────────────────────────────────────────────────────────

class TestGroupStats:
    def test_groups_by_key(self):
        records = [
            _make_record(action="long", actual_pnl_pct=5.0, actual_pnl_usdt=150),
            _make_record(action="long", actual_pnl_pct=-2.0, actual_pnl_usdt=-60),
            _make_record(action="short", actual_pnl_pct=3.0, actual_pnl_usdt=90),
        ]
        result = _group_stats(records, key=lambda r: r.action)
        assert result["long"]["count"] == 2
        assert result["short"]["count"] == 1
        assert result["long"]["win_rate"] == 0.5


# ─── _calc_risk_reward ────────────────────────────────────────────────────

class TestRiskReward:
    def test_basic(self):
        records = [
            _make_record(actual_pnl_pct=6.0),
            _make_record(actual_pnl_pct=4.0),
            _make_record(actual_pnl_pct=-2.0),
        ]
        result = _calc_risk_reward(records)
        assert result["avg_win_pct"] == 5.0
        assert result["avg_loss_pct"] == 2.0
        assert result["actual_risk_reward"] == 2.5

    def test_no_losses(self):
        records = [_make_record(actual_pnl_pct=5.0)]
        result = _calc_risk_reward(records)
        assert result["actual_risk_reward"] == "inf"


# ─── _calc_streak ─────────────────────────────────────────────────────────

class TestStreak:
    def test_streak(self):
        records = [
            _make_record(timestamp="2026-02-10T00:00:00+00:00", actual_pnl_pct=5.0),
            _make_record(timestamp="2026-02-11T00:00:00+00:00", actual_pnl_pct=3.0),
            _make_record(timestamp="2026-02-12T00:00:00+00:00", actual_pnl_pct=2.0),
            _make_record(timestamp="2026-02-13T00:00:00+00:00", actual_pnl_pct=-1.0),
            _make_record(timestamp="2026-02-14T00:00:00+00:00", actual_pnl_pct=-2.0),
        ]
        result = _calc_streak(records)
        assert result["max_consecutive_wins"] == 3
        assert result["max_consecutive_losses"] == 2


# ─── replay_signal ────────────────────────────────────────────────────────

class TestReplaySignal:
    def test_no_entry_range(self):
        record = _make_record(entry_price_range=[])
        assert replay_signal(record) is None

    @patch("cryptobot.indicators.calculator.load_klines")
    def test_long_replay(self, mock_klines):
        import pandas as pd
        import numpy as np

        # 模拟 K 线: 入场 95000, 最高 102000, 最低 93000
        dates = pd.date_range("2026-02-15", periods=24, freq="h", tz="UTC")
        df = pd.DataFrame({
            "open": np.linspace(95000, 100000, 24),
            "high": np.linspace(95500, 102000, 24),
            "low": np.linspace(93000, 98000, 24),
            "close": np.linspace(95000, 101000, 24),
            "volume": [100] * 24,
        }, index=dates)
        df.index.name = "datetime"
        mock_klines.return_value = df

        record = _make_record(
            entry_price_range=[94000, 96000],
            stop_loss=91000,
            take_profit=[{"price": 100000, "close_pct": 50}],
        )
        result = replay_signal(record)
        assert result is not None
        assert result["symbol"] == "BTCUSDT"
        assert result["mfe_pct"] > 0  # 有正向偏移
        assert result["sl_hit"] is False  # 最低 93000 > 止损 91000
        assert result["tp_hits"] == 1  # 最高 102000 > 止盈 100000


# ─── CLI ──────────────────────────────────────────────────────────────────

class TestBacktestCLI:
    def test_backtest_help(self):
        runner = CliRunner()
        result = runner.invoke(backtest, ["--help"])
        assert result.exit_code == 0
        assert "回测" in result.output

    def test_evaluate_help(self):
        runner = CliRunner()
        result = runner.invoke(backtest, ["evaluate", "--help"])
        assert result.exit_code == 0
        assert "--days" in result.output

    @patch("cryptobot.backtest.evaluator.get_all_records", return_value=[])
    def test_evaluate_empty_json(self, mock_get):
        runner = CliRunner()
        result = runner.invoke(backtest, ["evaluate", "--json-output"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["overview"]["total"] == 0

    def test_replay_help(self):
        runner = CliRunner()
        result = runner.invoke(backtest, ["replay", "--help"])
        assert result.exit_code == 0
