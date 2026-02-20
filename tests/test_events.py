"""事件驱动监控测试

覆盖: PriceTracker、事件检测、dispatcher、CLI
"""

import time
from unittest.mock import patch, MagicMock

import pytest
from click.testing import CliRunner

from cryptobot.events.price_monitor import (
    PriceTracker,
    PriceEvent,
    check_events,
    fetch_all_prices,
)
from cryptobot.events.dispatcher import handle_events, _notify_event
from cryptobot.cli.events import events


# ─── PriceTracker ─────────────────────────────────────────────────────────

class TestPriceTracker:
    def test_add_and_history(self):
        tracker = PriceTracker(symbol="BTCUSDT")
        tracker.add(95000)
        tracker.add(96000)
        assert len(tracker.history) == 2
        assert tracker.history[-1].price == 96000

    def test_change_pct_basic(self):
        tracker = PriceTracker(symbol="BTCUSDT")
        # 模拟: 第一个点 300s 前，第二个点现在
        now = time.time()
        tracker.history.append(
            MagicMock(price=100000, timestamp=now - 300)
        )
        tracker.history.append(
            MagicMock(price=97000, timestamp=now)
        )
        change = tracker.change_pct(300)
        assert change == pytest.approx(-3.0, abs=0.01)

    def test_change_pct_no_data(self):
        tracker = PriceTracker(symbol="BTCUSDT")
        assert tracker.change_pct(300) is None

    def test_change_pct_single_point(self):
        tracker = PriceTracker(symbol="BTCUSDT")
        tracker.add(95000)
        assert tracker.change_pct(300) is None

    def test_max_history_size(self):
        tracker = PriceTracker(symbol="BTCUSDT")
        for i in range(50):
            tracker.add(95000 + i)
        assert len(tracker.history) == 40  # maxlen=40


# ─── check_events ─────────────────────────────────────────────────────────

class TestCheckEvents:
    def test_no_events_normal_price(self):
        tracker = PriceTracker(symbol="BTCUSDT")
        now = time.time()
        tracker.history.append(MagicMock(price=95000, timestamp=now - 300))
        tracker.history.append(MagicMock(price=95100, timestamp=now))

        events = check_events(
            {"BTCUSDT": tracker},
            [(300, 3.0)],
        )
        assert len(events) == 0

    def test_crash_detected(self):
        tracker = PriceTracker(symbol="BTCUSDT")
        now = time.time()
        tracker.history.append(MagicMock(price=100000, timestamp=now - 300))
        tracker.history.append(MagicMock(price=96500, timestamp=now))

        events = check_events(
            {"BTCUSDT": tracker},
            [(300, 3.0)],
        )
        assert len(events) == 1
        assert events[0].direction == "crash"
        assert events[0].change_pct == -3.5
        assert events[0].symbol == "BTCUSDT"
        assert events[0].window_minutes == 5

    def test_spike_detected(self):
        tracker = PriceTracker(symbol="ETHUSDT")
        now = time.time()
        tracker.history.append(MagicMock(price=3000, timestamp=now - 300))
        tracker.history.append(MagicMock(price=3120, timestamp=now))

        events = check_events(
            {"ETHUSDT": tracker},
            [(300, 3.0)],
        )
        assert len(events) == 1
        assert events[0].direction == "spike"
        assert events[0].change_pct == 4.0

    def test_multiple_thresholds(self):
        tracker = PriceTracker(symbol="BTCUSDT")
        now = time.time()
        tracker.history.append(MagicMock(price=100000, timestamp=now - 900))
        tracker.history.append(MagicMock(price=100000, timestamp=now - 300))
        tracker.history.append(MagicMock(price=93000, timestamp=now))

        events = check_events(
            {"BTCUSDT": tracker},
            [(300, 3.0), (900, 5.0)],
        )
        # 5min -7% 触发 3% 阈值，15min -7% 触发 5% 阈值
        assert len(events) == 2


# ─── fetch_all_prices ─────────────────────────────────────────────────────

class TestFetchAllPrices:
    @patch("cryptobot.events.price_monitor.httpx.get")
    def test_fetches_and_filters(self, mock_get):
        mock_get.return_value = MagicMock(
            status_code=200,
            json=lambda: [
                {"symbol": "BTCUSDT", "price": "95000.50"},
                {"symbol": "ETHUSDT", "price": "3200.00"},
                {"symbol": "XRPUSDT", "price": "0.60"},
            ],
        )
        mock_get.return_value.raise_for_status = MagicMock()

        prices = fetch_all_prices(["BTCUSDT", "ETHUSDT"])
        assert prices["BTCUSDT"] == 95000.50
        assert prices["ETHUSDT"] == 3200.00
        assert "XRPUSDT" not in prices

    @patch("cryptobot.events.price_monitor.httpx.get", side_effect=Exception("timeout"))
    def test_returns_empty_on_error(self, mock_get):
        prices = fetch_all_prices(["BTCUSDT"])
        assert prices == {}


# ─── dispatcher ───────────────────────────────────────────────────────────

class TestDispatcher:
    @patch("cryptobot.notify.notify_alert")
    def test_notify_event_crash(self, mock_notify):
        event = PriceEvent(
            symbol="BTCUSDT", change_pct=-5.2, window_minutes=5,
            current_price=94800, direction="crash",
            timestamp="2026-02-20T00:00:00",
        )
        _notify_event(event)
        mock_notify.assert_called_once()
        call_args = mock_notify.call_args
        assert call_args[0][0] == "CRITICAL"  # >= 5%
        assert "BTCUSDT" in call_args[0][1]

    @patch("cryptobot.notify.notify_alert")
    def test_notify_event_spike_warning(self, mock_notify):
        event = PriceEvent(
            symbol="ETHUSDT", change_pct=3.5, window_minutes=5,
            current_price=3300, direction="spike",
            timestamp="2026-02-20T00:00:00",
        )
        _notify_event(event)
        call_args = mock_notify.call_args
        assert call_args[0][0] == "WARNING"  # < 5%

    @patch("cryptobot.events.dispatcher._handle_crash")
    @patch("cryptobot.events.dispatcher._handle_spike")
    @patch("cryptobot.events.dispatcher._notify_event")
    def test_handle_events_dispatches(self, mock_notify, mock_spike, mock_crash):
        crash_event = PriceEvent(
            symbol="BTCUSDT", change_pct=-4.0, window_minutes=5,
            current_price=94000, direction="crash",
            timestamp="2026-02-20T00:00:00",
        )
        spike_event = PriceEvent(
            symbol="ETHUSDT", change_pct=3.5, window_minutes=5,
            current_price=3300, direction="spike",
            timestamp="2026-02-20T00:00:00",
        )
        handle_events([crash_event, spike_event])

        mock_crash.assert_called_once_with(crash_event)
        mock_spike.assert_called_once_with(spike_event)
        assert mock_notify.call_count == 2


# ─── CLI ──────────────────────────────────────────────────────────────────

class TestEventsCLI:
    def test_events_help(self):
        runner = CliRunner()
        result = runner.invoke(events, ["--help"])
        assert result.exit_code == 0
        assert "价格异动" in result.output

    def test_start_help(self):
        runner = CliRunner()
        result = runner.invoke(events, ["start", "--help"])
        assert result.exit_code == 0
        assert "--verbose" in result.output

    def test_status_json(self):
        runner = CliRunner()
        result = runner.invoke(events, ["status", "--json-output"])
        assert result.exit_code == 0
        import json
        data = json.loads(result.output)
        assert "threshold_5min_pct" in data
        assert "poll_interval_seconds" in data
