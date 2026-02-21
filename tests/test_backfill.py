"""Journal backfill 测试"""

import json
from unittest.mock import MagicMock, patch

import numpy as np
import pandas as pd
import pytest
from click.testing import CliRunner

from cryptobot.journal.backfill import (
    BackfillResult,
    _build_analyst_votes,
    _calc_pnl,
    _calc_sl,
    _calc_tp,
    _make_signal_id,
    _score_to_confidence,
    _simulate_exit,
    run_backfill,
)


# ─── _make_signal_id ──────────────────────────────────────────────────────


class TestMakeSignalId:
    def test_length_12(self):
        sid = _make_signal_id("BTCUSDT", "2024-01-01")
        assert len(sid) == 12

    def test_idempotent(self):
        a = _make_signal_id("BTCUSDT", "2024-01-01")
        b = _make_signal_id("BTCUSDT", "2024-01-01")
        assert a == b

    def test_different_inputs(self):
        a = _make_signal_id("BTCUSDT", "2024-01-01")
        b = _make_signal_id("ETHUSDT", "2024-01-01")
        c = _make_signal_id("BTCUSDT", "2024-01-02")
        assert a != b
        assert a != c

    def test_hex_chars_only(self):
        sid = _make_signal_id("SOLUSDT", "2024-06-15T12:00:00")
        assert all(c in "0123456789abcdef" for c in sid)


# ─── _simulate_exit ───────────────────────────────────────────────────────


def _make_bars(data: list[dict]) -> pd.DataFrame:
    """构造 future_bars DataFrame"""
    df = pd.DataFrame(data)
    df.index = pd.date_range("2024-01-01", periods=len(data), freq="4h")
    return df


class TestSimulateExit:
    def test_long_sl_hit(self):
        bars = _make_bars([
            {"open": 100, "high": 101, "low": 94, "close": 95, "volume": 1},
        ])
        price, reason, held = _simulate_exit(bars, 100, 95, 110, "long")
        assert reason == "sl_hit"
        assert price == 95
        assert held == 1

    def test_long_tp_hit(self):
        bars = _make_bars([
            {"open": 100, "high": 112, "low": 99, "close": 110, "volume": 1},
        ])
        price, reason, held = _simulate_exit(bars, 100, 90, 110, "long")
        assert reason == "tp_hit"
        assert price == 110
        assert held == 1

    def test_long_sl_priority_over_tp(self):
        """同 K 线内 SL 优先于 TP"""
        bars = _make_bars([
            {"open": 100, "high": 115, "low": 88, "close": 100, "volume": 1},
        ])
        price, reason, held = _simulate_exit(bars, 100, 90, 110, "long")
        assert reason == "sl_hit"
        assert price == 90

    def test_short_sl_hit(self):
        bars = _make_bars([
            {"open": 100, "high": 106, "low": 99, "close": 105, "volume": 1},
        ])
        price, reason, held = _simulate_exit(bars, 100, 105, 90, "short")
        assert reason == "sl_hit"
        assert price == 105
        assert held == 1

    def test_short_tp_hit(self):
        bars = _make_bars([
            {"open": 100, "high": 101, "low": 88, "close": 90, "volume": 1},
        ])
        price, reason, held = _simulate_exit(bars, 100, 110, 90, "short")
        assert reason == "tp_hit"
        assert price == 90
        assert held == 1

    def test_timeout(self):
        bars = _make_bars([
            {"open": 100, "high": 102, "low": 98, "close": 101, "volume": 1},
            {"open": 101, "high": 103, "low": 99, "close": 102, "volume": 1},
        ])
        price, reason, held = _simulate_exit(bars, 100, 90, 115, "long")
        assert reason == "timeout"
        assert price == 102  # 最后一根收盘价
        assert held == 2

    def test_empty_bars(self):
        bars = _make_bars([])
        price, reason, held = _simulate_exit(bars, 100, 90, 110, "long")
        assert reason == "timeout"
        assert price == 100
        assert held == 0

    def test_bars_held_increments(self):
        bars = _make_bars([
            {"open": 100, "high": 101, "low": 99, "close": 100, "volume": 1},
            {"open": 100, "high": 101, "low": 99, "close": 100, "volume": 1},
            {"open": 100, "high": 111, "low": 99, "close": 110, "volume": 1},
        ])
        _, reason, held = _simulate_exit(bars, 100, 90, 110, "long")
        assert reason == "tp_hit"
        assert held == 3


# ─── _calc_pnl ────────────────────────────────────────────────────────────


class TestCalcPnl:
    def test_long_profit(self):
        pnl = _calc_pnl(100, 110, "long", 1)
        assert pnl == pytest.approx(10.0)

    def test_long_loss(self):
        pnl = _calc_pnl(100, 95, "long", 1)
        assert pnl == pytest.approx(-5.0)

    def test_short_profit(self):
        pnl = _calc_pnl(100, 90, "short", 1)
        assert pnl == pytest.approx(10.0)

    def test_short_loss(self):
        pnl = _calc_pnl(100, 105, "short", 1)
        assert pnl == pytest.approx(-5.0)

    def test_leverage_amplifies(self):
        pnl = _calc_pnl(100, 110, "long", 3)
        assert pnl == pytest.approx(30.0)

    def test_zero_entry(self):
        pnl = _calc_pnl(0, 100, "long", 1)
        assert pnl == 0.0


# ─── _score_to_confidence ─────────────────────────────────────────────────


class TestScoreToConfidence:
    def test_high_score(self):
        assert _score_to_confidence(7) == 85
        assert _score_to_confidence(10) == 85

    def test_medium_score(self):
        assert _score_to_confidence(5) == 75
        assert _score_to_confidence(6.5) == 75

    def test_low_score(self):
        assert _score_to_confidence(3) == 70
        assert _score_to_confidence(4) == 70

    def test_below_threshold(self):
        assert _score_to_confidence(2) == 65
        assert _score_to_confidence(1) == 65


# ─── _build_analyst_votes ─────────────────────────────────────────────────


class TestBuildAnalystVotes:
    def test_has_four_roles(self):
        votes = _build_analyst_votes("bullish")
        assert set(votes.keys()) == {"technical", "onchain", "fundamental", "news"}

    def test_technical_matches_bias(self):
        assert _build_analyst_votes("bullish")["technical"] == "bullish"
        assert _build_analyst_votes("bearish")["technical"] == "bearish"

    def test_others_neutral(self):
        votes = _build_analyst_votes("bullish")
        assert votes["onchain"] == "neutral"
        assert votes["fundamental"] == "neutral"
        assert votes["news"] == "neutral"


# ─── _calc_sl / _calc_tp ─────────────────────────────────────────────────


class TestCalcSlTp:
    def test_long_sl(self):
        sl = _calc_sl(100, 10, "long")
        assert sl == pytest.approx(85.0)  # 100 - 10*1.5

    def test_short_sl(self):
        sl = _calc_sl(100, 10, "short")
        assert sl == pytest.approx(115.0)  # 100 + 10*1.5

    def test_long_tp(self):
        tp = _calc_tp(100, 10, "long")
        assert tp == pytest.approx(120.0)  # 100 + 10*2.0

    def test_short_tp(self):
        tp = _calc_tp(100, 10, "short")
        assert tp == pytest.approx(80.0)  # 100 - 10*2.0


# ─── run_backfill 集成测试 ────────────────────────────────────────────────


def _make_kline_df(n: int = 200, base_price: float = 100.0) -> pd.DataFrame:
    """生成模拟 K 线 DataFrame"""
    np.random.seed(42)
    dates = pd.date_range("2024-01-01", periods=n, freq="4h")
    prices = base_price + np.cumsum(np.random.randn(n) * 2)
    prices = np.maximum(prices, 10)  # 保证正数

    df = pd.DataFrame({
        "open": prices,
        "high": prices + np.abs(np.random.randn(n)) * 3,
        "low": prices - np.abs(np.random.randn(n)) * 3,
        "close": prices + np.random.randn(n) * 1,
        "volume": np.random.rand(n) * 1000 + 100,
    }, index=dates)

    # 保证 high >= close/open, low <= close/open
    df["high"] = df[["open", "high", "close"]].max(axis=1)
    df["low"] = df[["open", "low", "close"]].min(axis=1)

    return df


class TestRunBackfill:
    @patch("cryptobot.journal.backfill.download_klines")
    @patch("cryptobot.journal.backfill.get_record", return_value=None)
    @patch("cryptobot.journal.backfill.save_record")
    def test_basic_run(self, mock_save, mock_get, mock_dl):
        mock_dl.return_value = _make_kline_df(300)

        result = run_backfill(days=60, symbols=["BTCUSDT"])

        assert isinstance(result, BackfillResult)
        assert result.total_generated >= 0
        assert result.errors == []
        assert "BTCUSDT" in result.by_symbol

    @patch("cryptobot.journal.backfill.download_klines")
    @patch("cryptobot.journal.backfill.get_record", return_value=None)
    @patch("cryptobot.journal.backfill.save_record")
    def test_dry_run_no_save(self, mock_save, mock_get, mock_dl):
        mock_dl.return_value = _make_kline_df(300)

        result = run_backfill(days=60, symbols=["BTCUSDT"], dry_run=True)

        mock_save.assert_not_called()
        assert result.total_generated >= 0

    @patch("cryptobot.journal.backfill.download_klines")
    def test_download_failure_tolerant(self, mock_dl):
        mock_dl.side_effect = Exception("API error")

        result = run_backfill(days=60, symbols=["BTCUSDT"])

        assert len(result.errors) == 1
        assert "API error" in result.errors[0]
        assert result.by_symbol["BTCUSDT"] == 0

    @patch("cryptobot.journal.backfill.download_klines")
    @patch("cryptobot.journal.backfill.get_record", return_value=None)
    @patch("cryptobot.journal.backfill.save_record")
    def test_record_structure(self, mock_save, mock_get, mock_dl):
        mock_dl.return_value = _make_kline_df(300)

        run_backfill(days=60, symbols=["BTCUSDT"])

        if mock_save.call_count > 0:
            record = mock_save.call_args[0][0]
            assert record.status == "closed"
            assert record.symbol == "BTCUSDT"
            assert record.action in ("long", "short")
            assert record.actual_entry_price is not None
            assert record.actual_exit_price is not None
            assert record.actual_pnl_pct is not None
            assert record.exit_reason in ("sl_hit", "tp_hit", "timeout")
            assert record.prompt_version == "backfill-v1"
            assert record.analyst_votes is not None
            assert len(record.signal_id) == 12

    @patch("cryptobot.journal.backfill.download_klines")
    @patch("cryptobot.journal.backfill.save_record")
    def test_idempotent_skip_existing(self, mock_save, mock_dl):
        """已存在的记录不重复写入"""
        mock_dl.return_value = _make_kline_df(300)

        existing = MagicMock()
        with patch("cryptobot.journal.backfill.get_record", return_value=existing):
            run_backfill(days=60, symbols=["BTCUSDT"])

        mock_save.assert_not_called()

    @patch("cryptobot.journal.backfill.download_klines")
    @patch("cryptobot.journal.backfill.get_record", return_value=None)
    @patch("cryptobot.journal.backfill.save_record")
    def test_insufficient_klines(self, mock_save, mock_get, mock_dl):
        """K 线不足时跳过"""
        mock_dl.return_value = _make_kline_df(50)

        result = run_backfill(days=60, symbols=["BTCUSDT"])

        assert result.total_generated == 0
        mock_save.assert_not_called()


# ─── CLI 测试 ─────────────────────────────────────────────────────────────


class TestBackfillCli:
    def test_help(self):
        from cryptobot.cli.journal import journal

        runner = CliRunner()
        result = runner.invoke(journal, ["backfill", "--help"])
        assert result.exit_code == 0
        assert "历史信号回填" in result.output
        assert "--dry-run" in result.output
        assert "--days" in result.output
        assert "--symbol" in result.output

    @patch("cryptobot.journal.backfill.download_klines")
    @patch("cryptobot.journal.backfill.get_record", return_value=None)
    @patch("cryptobot.journal.backfill.save_record")
    def test_dry_run_cli(self, mock_save, mock_get, mock_dl):
        from cryptobot.cli.journal import journal

        mock_dl.return_value = _make_kline_df(300)

        runner = CliRunner()
        result = runner.invoke(
            journal, ["backfill", "--dry-run", "--symbol", "BTCUSDT"],
        )
        assert result.exit_code == 0
        assert "预览模式" in result.output
        mock_save.assert_not_called()

    @patch("cryptobot.journal.backfill.download_klines")
    @patch("cryptobot.journal.backfill.get_record", return_value=None)
    @patch("cryptobot.journal.backfill.save_record")
    def test_json_output(self, mock_save, mock_get, mock_dl):
        from cryptobot.cli.journal import journal

        mock_dl.return_value = _make_kline_df(300)

        runner = CliRunner()
        result = runner.invoke(
            journal,
            ["backfill", "--json-output", "--symbol", "BTCUSDT", "--dry-run"],
        )
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert "total_generated" in data
        assert "win_rate" in data
        assert "by_symbol" in data

    @patch("cryptobot.journal.backfill.download_klines")
    @patch("cryptobot.journal.backfill.get_record", return_value=None)
    @patch("cryptobot.journal.backfill.save_record")
    def test_symbol_filter(self, mock_save, mock_get, mock_dl):
        from cryptobot.cli.journal import journal

        mock_dl.return_value = _make_kline_df(300)

        runner = CliRunner()
        result = runner.invoke(
            journal,
            ["backfill", "--symbol", "BTCUSDT", "--symbol", "ETHUSDT", "--dry-run"],
        )
        assert result.exit_code == 0
        # download_klines 应该只被调用 2 次 (2 个币种)
        assert mock_dl.call_count == 2
