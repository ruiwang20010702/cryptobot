"""historical_replay 单元测试

覆盖: ReplayConfig/ReplaySnapshot 数据结构、K线切片、快照构建、
      信号解析、断点续跑、完整流程 mock 测试。
"""

import json
from datetime import datetime, timedelta, timezone
from unittest.mock import patch, MagicMock

import numpy as np
import pandas as pd
import pytest

from cryptobot.backtest.historical_replay import (
    ReplayConfig,
    ReplaySnapshot,
    _slice_klines_at,
    _build_snapshot,
    _parse_to_signal,
    _save_progress,
    _load_progress,
    _clear_progress,
    _format_snapshot_prompt,
    _download_full_klines,
    run_historical_replay,
    _PROGRESS_FILE,
    TIMEFRAMES,
    _MIN_BARS,
)


# ── 辅助 ──────────────────────────────────────────────────────────────────


def _make_klines(n: int = 200, start: str = "2025-06-01", freq: str = "h") -> pd.DataFrame:
    """构建带趋势的 K 线，满足 TA-Lib 指标计算需求"""
    ts = pd.date_range(start, periods=n, freq=freq)
    base = 100.0
    rng = np.random.RandomState(42)
    close = base + np.cumsum(rng.randn(n) * 0.5) + np.arange(n) * 0.01
    close = np.maximum(close, 10.0)
    high = close + rng.uniform(0.5, 2.0, n)
    low = close - rng.uniform(0.5, 2.0, n)
    low = np.maximum(low, 1.0)
    open_ = close + rng.uniform(-0.5, 0.5, n)

    return pd.DataFrame(
        {"open": open_, "high": high, "low": low, "close": close, "volume": rng.uniform(100, 1000, n)},
        index=ts,
    )


def _make_cache(
    symbols: list[str] | None = None,
    days: int = 150,
) -> dict[tuple[str, str], pd.DataFrame]:
    """构建覆盖 days 天的 klines_cache，所有 TF 均有足够数据"""
    if symbols is None:
        symbols = ["BTCUSDT"]

    freq_map = {"1h": "h", "4h": "4h", "1d": "D"}
    bars_per_day = {"1h": 24, "4h": 6, "1d": 1}

    cache = {}
    for sym in symbols:
        for tf in TIMEFRAMES:
            n = days * bars_per_day[tf]
            cache[(sym, tf)] = _make_klines(n, freq=freq_map[tf])
    return cache


def _make_snapshot(
    symbol: str = "BTCUSDT",
    as_of: str = "2025-12-01T00:00:00",
    price: float = 100.0,
) -> ReplaySnapshot:
    return ReplaySnapshot(
        symbol=symbol,
        as_of=as_of,
        current_price=price,
        tech_indicators={"latest_close": price, "trend": {"ema_7": price}},
        multi_timeframe={"aligned_direction": "bullish"},
        support_resistance={"nearest_support": price * 0.95},
    )


# ── TestReplayConfig ─────────────────────────────────────────────────────


class TestReplayConfig:
    def test_defaults(self):
        cfg = ReplayConfig()
        assert cfg.days == 90
        assert cfg.symbols == []
        assert cfg.interval_hours == 24
        assert cfg.llm_model == "sonnet"
        assert cfg.max_concurrent == 5
        assert cfg.initial_capital == 10000.0

    def test_custom(self):
        cfg = ReplayConfig(
            days=30,
            symbols=["BTCUSDT", "ETHUSDT"],
            interval_hours=12,
            max_leverage=3,
        )
        assert cfg.days == 30
        assert len(cfg.symbols) == 2
        assert cfg.interval_hours == 12
        assert cfg.max_leverage == 3

    def test_frozen(self):
        cfg = ReplayConfig()
        with pytest.raises(AttributeError):
            cfg.days = 30


# ── TestSliceKlines ──────────────────────────────────────────────────────


class TestSliceKlines:
    def test_basic_slice(self):
        """在所有 TF 都有 100+ 根的时间点切片"""
        cache = _make_cache(days=150)
        # 取 120 天位置 (1d 有 120 根, 4h 有 720 根, 1h 有 2880 根 → 都 > 100)
        as_of_dt = cache[("BTCUSDT", "1d")].index[119].to_pydatetime()

        result = _slice_klines_at(cache, "BTCUSDT", as_of_dt)
        assert result is not None
        for tf in TIMEFRAMES:
            key = ("BTCUSDT", tf)
            assert key in result
            assert len(result[key]) >= _MIN_BARS
            # 所有数据应 <= as_of
            assert result[key].index[-1] <= as_of_dt

    def test_insufficient_data(self):
        """K 线不足 _MIN_BARS 时返回 None"""
        cache = _make_cache(days=150)
        # 取非常早的时间点 (1d 只有 ~10 根)
        as_of_dt = cache[("BTCUSDT", "1d")].index[10].to_pydatetime()
        result = _slice_klines_at(cache, "BTCUSDT", as_of_dt)
        # 1d 只有 11 根 < 100 → None
        assert result is None

    def test_missing_symbol(self):
        cache = _make_cache(days=150)
        as_of = datetime(2025, 11, 1)
        result = _slice_klines_at(cache, "XYZUSDT", as_of)
        assert result is None

    def test_timezone_handling(self):
        """带 timezone 的 as_of 也能正确切片"""
        cache = _make_cache(days=150)
        as_of = cache[("BTCUSDT", "1d")].index[119].to_pydatetime()
        as_of_tz = as_of.replace(tzinfo=timezone.utc)

        result = _slice_klines_at(cache, "BTCUSDT", as_of_tz)
        assert result is not None


# ── TestBuildSnapshot ────────────────────────────────────────────────────


class TestBuildSnapshot:
    @patch("cryptobot.indicators.multi_timeframe.calc_support_resistance")
    @patch("cryptobot.indicators.multi_timeframe.calc_multi_timeframe")
    @patch("cryptobot.indicators.calculator.calc_all_indicators")
    def test_builds_correctly(self, mock_calc, mock_mtf, mock_sr):
        mock_calc.return_value = {
            "latest_close": 100.0,
            "trend": {"ema_7": 100.0},
        }
        mock_mtf.return_value = {"aligned_direction": "bullish"}
        mock_sr.return_value = {"nearest_support": 95.0}

        sliced = {("BTCUSDT", tf): _make_klines(200) for tf in TIMEFRAMES}
        as_of = datetime(2025, 12, 1)

        snap = _build_snapshot("BTCUSDT", as_of, sliced)

        assert snap is not None
        assert snap.symbol == "BTCUSDT"
        assert snap.current_price == 100.0
        assert snap.tech_indicators["trend"]["ema_7"] == 100.0
        mock_calc.assert_called_once()
        mock_mtf.assert_called_once()
        mock_sr.assert_called_once()

    @patch("cryptobot.indicators.calculator.calc_all_indicators")
    def test_returns_none_on_error(self, mock_calc):
        mock_calc.side_effect = Exception("No data")
        sliced = {("BTCUSDT", tf): _make_klines(200) for tf in TIMEFRAMES}
        snap = _build_snapshot("BTCUSDT", datetime(2025, 12, 1), sliced)
        assert snap is None

    @patch("cryptobot.indicators.multi_timeframe.calc_support_resistance")
    @patch("cryptobot.indicators.multi_timeframe.calc_multi_timeframe")
    @patch("cryptobot.indicators.calculator.calc_all_indicators")
    def test_returns_none_when_no_price(self, mock_calc, mock_mtf, mock_sr):
        mock_calc.return_value = {"latest_close": 0}
        mock_mtf.return_value = {}
        mock_sr.return_value = {}

        sliced = {("BTCUSDT", tf): _make_klines(200) for tf in TIMEFRAMES}
        snap = _build_snapshot("BTCUSDT", datetime(2025, 12, 1), sliced)
        assert snap is None


# ── TestParseToSignal ────────────────────────────────────────────────────


class TestParseToSignal:
    def _snapshot(self, symbol="BTCUSDT", price=100.0):
        return _make_snapshot(symbol=symbol, price=price)

    def test_valid_long(self):
        llm = {
            "action": "long",
            "entry_price_range": [99.0, 101.0],
            "stop_loss": 95.0,
            "take_profit": [{"price": 105.0, "ratio": 0.5}, {"price": 110.0, "ratio": 0.5}],
            "leverage": 3,
            "confidence": 70,
            "reasoning": "Bullish trend",
        }
        sig = _parse_to_signal(llm, self._snapshot())
        assert sig is not None
        assert sig["action"] == "long"
        assert sig["symbol"] == "BTCUSDT"
        assert sig["confidence"] == 70
        assert sig["signal_source"] == "replay"
        assert len(sig["take_profit"]) == 2

    def test_valid_short(self):
        llm = {
            "action": "short",
            "entry_price_range": [99.0, 101.0],
            "stop_loss": 105.0,
            "take_profit": [95.0, 90.0],
            "leverage": 2,
            "confidence": 65,
            "reasoning": "Bearish",
        }
        sig = _parse_to_signal(llm, self._snapshot())
        assert sig is not None
        assert sig["action"] == "short"
        assert sig["stop_loss"] == 105.0
        assert all(isinstance(tp, dict) for tp in sig["take_profit"])

    def test_no_trade(self):
        llm = {"action": "no_trade", "confidence": 40, "reasoning": "No signal"}
        sig = _parse_to_signal(llm, self._snapshot())
        assert sig is None

    def test_low_confidence(self):
        llm = {
            "action": "long",
            "entry_price_range": [99.0, 101.0],
            "stop_loss": 95.0,
            "confidence": 50,
            "reasoning": "Weak",
        }
        sig = _parse_to_signal(llm, self._snapshot())
        assert sig is None

    def test_wrong_stop_loss_direction(self):
        llm = {
            "action": "long",
            "entry_price_range": [99.0, 101.0],
            "stop_loss": 105.0,
            "confidence": 70,
            "reasoning": "Test",
        }
        sig = _parse_to_signal(llm, self._snapshot())
        assert sig is None

    def test_short_wrong_stop(self):
        llm = {
            "action": "short",
            "entry_price_range": [99.0, 101.0],
            "stop_loss": 95.0,
            "confidence": 70,
            "reasoning": "Test",
        }
        sig = _parse_to_signal(llm, self._snapshot())
        assert sig is None

    def test_invalid_output_none(self):
        assert _parse_to_signal(None, self._snapshot()) is None

    def test_invalid_output_string(self):
        assert _parse_to_signal("not json", self._snapshot()) is None

    def test_json_string(self):
        llm_str = json.dumps({
            "action": "long",
            "entry_price_range": [99.0, 101.0],
            "stop_loss": 95.0,
            "take_profit": [105.0],
            "leverage": 2,
            "confidence": 60,
            "reasoning": "OK",
        })
        sig = _parse_to_signal(llm_str, self._snapshot())
        assert sig is not None

    def test_missing_entry_range(self):
        llm = {"action": "long", "stop_loss": 95.0, "confidence": 70, "reasoning": "X"}
        sig = _parse_to_signal(llm, self._snapshot())
        assert sig is None

    def test_missing_stop_loss(self):
        llm = {
            "action": "long",
            "entry_price_range": [99.0, 101.0],
            "confidence": 70,
            "reasoning": "X",
        }
        sig = _parse_to_signal(llm, self._snapshot())
        assert sig is None

    def test_leverage_capped(self):
        llm = {
            "action": "long",
            "entry_price_range": [99.0, 101.0],
            "stop_loss": 95.0,
            "leverage": 20,
            "confidence": 70,
            "reasoning": "X",
        }
        sig = _parse_to_signal(llm, self._snapshot())
        assert sig is not None
        assert sig["leverage"] <= 5

    def test_error_output_dict(self):
        sig = _parse_to_signal({"error": "timeout"}, self._snapshot())
        assert sig is None


# ── TestProgress ─────────────────────────────────────────────────────────


class TestProgress:
    def test_save_and_load(self, tmp_path, monkeypatch):
        monkeypatch.setattr(
            "cryptobot.backtest.historical_replay._PROGRESS_DIR", tmp_path,
        )
        monkeypatch.setattr(
            "cryptobot.backtest.historical_replay._PROGRESS_FILE",
            tmp_path / "replay_progress.json",
        )

        config = ReplayConfig(days=30, symbols=["BTCUSDT"])
        signals = [{"symbol": "BTCUSDT", "action": "long"}]
        dates = ["2025-12-01T00:00:00"]

        _save_progress(signals, dates, config)

        loaded_sigs, loaded_dates = _load_progress(config)
        assert len(loaded_sigs) == 1
        assert loaded_dates == dates

    def test_config_mismatch_returns_empty(self, tmp_path, monkeypatch):
        monkeypatch.setattr(
            "cryptobot.backtest.historical_replay._PROGRESS_DIR", tmp_path,
        )
        monkeypatch.setattr(
            "cryptobot.backtest.historical_replay._PROGRESS_FILE",
            tmp_path / "replay_progress.json",
        )

        config1 = ReplayConfig(days=30, symbols=["BTCUSDT"])
        _save_progress([{"x": 1}], ["2025-12-01"], config1)

        config2 = ReplayConfig(days=60, symbols=["BTCUSDT"])
        sigs, dates = _load_progress(config2)
        assert sigs == []
        assert dates == []

    def test_load_no_file(self, tmp_path, monkeypatch):
        monkeypatch.setattr(
            "cryptobot.backtest.historical_replay._PROGRESS_FILE",
            tmp_path / "nonexistent.json",
        )
        sigs, dates = _load_progress(ReplayConfig())
        assert sigs == []


# ── TestFormatPrompt ─────────────────────────────────────────────────────


class TestFormatPrompt:
    def test_contains_key_info(self):
        snap = _make_snapshot(symbol="ETHUSDT", price=3500.0)
        prompt = _format_snapshot_prompt(snap, max_leverage=5)
        assert "ETHUSDT" in prompt
        assert "3500.0" in prompt
        assert "5x" in prompt


# ── TestDownloadFullKlines ───────────────────────────────────────────────


class TestDownloadFullKlines:
    @patch("cryptobot.indicators.calculator.download_klines")
    def test_downloads_all_tfs(self, mock_dl):
        mock_dl.return_value = _make_klines(100)
        result = _download_full_klines(["BTCUSDT"])
        assert len(result) == 3
        assert mock_dl.call_count == 3

    @patch("cryptobot.indicators.calculator.download_klines")
    def test_handles_failure(self, mock_dl):
        mock_dl.side_effect = Exception("Network error")
        result = _download_full_klines(["BTCUSDT"])
        assert len(result) == 0


# ── TestRunReplayMocked ──────────────────────────────────────────────────


class TestRunReplayMocked:
    @patch("cryptobot.backtest.historical_replay._clear_progress")
    @patch("cryptobot.backtest.trade_simulator.simulate_trade")
    @patch("cryptobot.indicators.calculator.download_klines")
    @patch("cryptobot.backtest.historical_replay._run_llm_batch")
    @patch("cryptobot.backtest.historical_replay._build_snapshot")
    @patch("cryptobot.backtest.historical_replay._download_full_klines")
    @patch("cryptobot.backtest.historical_replay.get_all_symbols")
    def test_full_flow(
        self, mock_symbols, mock_dl_full, mock_build_snap,
        mock_llm_batch, mock_dl_klines, mock_sim, mock_clear,
    ):
        mock_symbols.return_value = ["BTCUSDT", "ETHUSDT"]

        # Phase 1: K 线缓存
        cache = _make_cache(["BTCUSDT", "ETHUSDT"], days=150)
        mock_dl_full.return_value = cache

        # Phase 2: 快照
        mock_build_snap.side_effect = lambda sym, as_of, sliced: _make_snapshot(
            symbol=sym, as_of=as_of.isoformat(),
        )

        # Phase 3: LLM 返回有效信号
        def fake_llm(snapshots, config):
            results = []
            for snap in snapshots:
                price = snap.current_price
                results.append({
                    "action": "long",
                    "entry_price_range": [price * 0.99, price * 1.01],
                    "stop_loss": price * 0.95,
                    "take_profit": [
                        {"price": price * 1.05, "ratio": 0.5},
                        {"price": price * 1.10, "ratio": 0.5},
                    ],
                    "leverage": 3,
                    "confidence": 70,
                    "reasoning": "Test signal",
                })
            return results

        mock_llm_batch.side_effect = fake_llm

        # Phase 4: 1h K 线下载 + 模拟
        mock_dl_klines.return_value = _make_klines(500)

        from cryptobot.backtest.trade_simulator import TradeResult

        mock_sim.return_value = TradeResult(
            symbol="BTCUSDT", action="long",
            entry_price=100.0, exit_price=105.0,
            leverage=3, confidence=70,
            gross_pnl_pct=15.0, costs_pct=0.5, net_pnl_pct=14.5,
            net_pnl_usdt=145.0,
            exit_reason="tp_full", mfe_pct=16.0, mae_pct=2.0,
            duration_hours=48.0,
            entry_time="2025-12-01T00:00:00", exit_time="2025-12-03T00:00:00",
            signal_source="replay",
        )

        config = ReplayConfig(days=5, symbols=["BTCUSDT", "ETHUSDT"])
        report = run_historical_replay(config)

        assert report is not None
        assert report.signal_source == "replay"
        assert report.metrics.total_trades > 0
        assert mock_dl_full.called
        assert mock_llm_batch.called
        mock_clear.assert_called_once()

    @patch("cryptobot.backtest.historical_replay._download_full_klines")
    @patch("cryptobot.backtest.historical_replay.get_all_symbols")
    def test_empty_klines(self, mock_symbols, mock_dl_full):
        mock_symbols.return_value = ["BTCUSDT"]
        mock_dl_full.return_value = {}

        config = ReplayConfig(days=5)
        report = run_historical_replay(config)
        assert report.metrics.total_trades == 0

    @patch("cryptobot.backtest.historical_replay._clear_progress")
    @patch("cryptobot.backtest.trade_simulator.simulate_trade")
    @patch("cryptobot.indicators.calculator.download_klines")
    @patch("cryptobot.backtest.historical_replay._run_llm_batch")
    @patch("cryptobot.backtest.historical_replay._build_snapshot")
    @patch("cryptobot.backtest.historical_replay._download_full_klines")
    @patch("cryptobot.backtest.historical_replay.get_all_symbols")
    def test_callback_invoked(
        self, mock_symbols, mock_dl_full, mock_build_snap,
        mock_llm_batch, mock_dl_klines, mock_sim, mock_clear,
    ):
        mock_symbols.return_value = ["BTCUSDT"]
        mock_dl_full.return_value = _make_cache(["BTCUSDT"], days=150)
        mock_build_snap.return_value = _make_snapshot()
        mock_llm_batch.return_value = [{"action": "no_trade", "confidence": 30, "reasoning": "X"}]
        mock_dl_klines.return_value = _make_klines(500)
        mock_sim.return_value = None

        callback_calls = []

        def on_day(idx, total, date_str, n_sig):
            callback_calls.append((idx, date_str, n_sig))

        config = ReplayConfig(days=3, symbols=["BTCUSDT"])
        run_historical_replay(config, on_day_done=on_day)

        assert len(callback_calls) > 0
