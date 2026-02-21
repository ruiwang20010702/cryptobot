"""实时入场监控测试"""

import json
from datetime import datetime, timezone, timedelta
from unittest.mock import MagicMock

import pytest

from cryptobot.signal.bridge import write_pending_signal, read_pending_signals


# ─── Fixtures ────────────────────────────────────────────────────────────

@pytest.fixture
def signal_env(tmp_path, monkeypatch):
    """使用临时目录作为信号目录"""
    import cryptobot.signal.bridge as bridge_mod

    sig_dir = tmp_path / "signals"
    sig_dir.mkdir()
    monkeypatch.setattr(bridge_mod, "SIGNAL_DIR", sig_dir)
    monkeypatch.setattr(bridge_mod, "SIGNAL_FILE", sig_dir / "signal.json")
    monkeypatch.setattr(bridge_mod, "PENDING_FILE", sig_dir / "pending_signals.json")
    return sig_dir


@pytest.fixture
def mock_settings(monkeypatch):
    """mock load_settings 返回默认实时配置"""
    import cryptobot.realtime.monitor as mon_mod

    settings = {
        "realtime": {
            "poll_interval_seconds": 1,
            "price_tolerance_pct": 0.1,
            "require_indicator_confirm": True,
            "max_wait_minutes": 120,
        }
    }
    monkeypatch.setattr(mon_mod, "load_settings", lambda: settings)
    return settings


def _make_pending_signal(symbol="BTCUSDT", action="long", entry_range=None, **overrides):
    """创建测试用 pending 信号"""
    now = datetime.now(timezone.utc)
    sig = {
        "symbol": symbol,
        "action": action,
        "leverage": 3,
        "entry_price_range": entry_range or [44000, 46000],
        "stop_loss": 42000 if action == "long" else 48000,
        "take_profit": [{"price": 50000, "pct": 100}],
        "confidence": 75,
        "expires_at": (now + timedelta(hours=4)).isoformat(),
    }
    sig.update(overrides)
    return sig


# ─── TestFetchPrice ──────────────────────────────────────────────────────

class TestFetchPrice:
    def test_success(self, monkeypatch):
        from cryptobot.realtime.monitor import _fetch_price

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = {"price": "45000.0"}

        import httpx
        monkeypatch.setattr(httpx, "get", lambda *a, **kw: mock_resp)

        price = _fetch_price("BTCUSDT")
        assert price == 45000.0

    def test_timeout(self, monkeypatch):
        from cryptobot.realtime.monitor import _fetch_price
        import httpx

        def _raise_timeout(*a, **kw):
            raise httpx.TimeoutException("timeout")

        monkeypatch.setattr(httpx, "get", _raise_timeout)
        assert _fetch_price("BTCUSDT") is None

    def test_http_error(self, monkeypatch):
        from cryptobot.realtime.monitor import _fetch_price
        import httpx

        mock_resp = MagicMock()
        mock_resp.raise_for_status.side_effect = httpx.HTTPStatusError(
            "500", request=MagicMock(), response=MagicMock()
        )
        monkeypatch.setattr(httpx, "get", lambda *a, **kw: mock_resp)

        assert _fetch_price("BTCUSDT") is None


# ─── TestCheckEntry ──────────────────────────────────────────────────────

class TestCheckEntry:
    def test_price_in_range(self):
        from cryptobot.realtime.monitor import _check_entry

        signal = {"entry_price_range": [44000, 46000]}
        assert _check_entry(signal, 45000) is True

    def test_price_out_of_range(self):
        from cryptobot.realtime.monitor import _check_entry

        signal = {"entry_price_range": [44000, 46000]}
        assert _check_entry(signal, 43000) is False

    def test_tolerance_expansion(self):
        from cryptobot.realtime.monitor import _check_entry

        signal = {"entry_price_range": [44000, 46000]}
        # tolerance_pct=0.1 → margin = 2000 * 0.1 / 100 = 2.0
        # 低边界: 44000 - 2 = 43998
        assert _check_entry(signal, 43998, tolerance_pct=0.1) is True

    def test_no_entry_range(self):
        from cryptobot.realtime.monitor import _check_entry

        signal = {"entry_price_range": None}
        assert _check_entry(signal, 45000) is True

    def test_empty_entry_range(self):
        from cryptobot.realtime.monitor import _check_entry

        signal = {}
        assert _check_entry(signal, 45000) is True


# ─── TestConfirmIndicators ───────────────────────────────────────────────

class TestConfirmIndicators:
    def test_long_confirmed(self, monkeypatch):
        """EMA 对齐 + RSI 正常 → score=2, 通过"""
        from cryptobot.realtime import monitor as mon_mod

        monkeypatch.setattr(
            mon_mod, "_load_5m_indicators",
            lambda s: {"rsi": 60, "ema7": 100, "ema25": 95},
        )
        assert mon_mod._confirm_indicators("BTCUSDT", "long") is True

    def test_long_overbought_extreme(self, monkeypatch):
        """EMA 对齐(+2) + RSI>=80(-3) → score=-1, 不通过"""
        from cryptobot.realtime import monitor as mon_mod

        monkeypatch.setattr(
            mon_mod, "_load_5m_indicators",
            lambda s: {"rsi": 85, "ema7": 100, "ema25": 95},
        )
        assert mon_mod._confirm_indicators("BTCUSDT", "long") is False

    def test_long_overbought_moderate(self, monkeypatch):
        """EMA 对齐(+2) + RSI 70-80(-1) → score=1, 通过"""
        from cryptobot.realtime import monitor as mon_mod

        monkeypatch.setattr(
            mon_mod, "_load_5m_indicators",
            lambda s: {"rsi": 75, "ema7": 100, "ema25": 95},
        )
        assert mon_mod._confirm_indicators("BTCUSDT", "long") is True

    def test_long_downtrend_neutral_rsi(self, monkeypatch):
        """EMA 反向(-1) + RSI 中性(0) → score=-1, 不通过"""
        from cryptobot.realtime import monitor as mon_mod

        monkeypatch.setattr(
            mon_mod, "_load_5m_indicators",
            lambda s: {"rsi": 50, "ema7": 90, "ema25": 95},
        )
        assert mon_mod._confirm_indicators("BTCUSDT", "long") is False

    def test_long_downtrend_low_rsi(self, monkeypatch):
        """EMA 反向(-1) + RSI<40(+1) → score=0, 通过"""
        from cryptobot.realtime import monitor as mon_mod

        monkeypatch.setattr(
            mon_mod, "_load_5m_indicators",
            lambda s: {"rsi": 35, "ema7": 99, "ema25": 100},
        )
        assert mon_mod._confirm_indicators("BTCUSDT", "long") is True

    def test_short_confirmed(self, monkeypatch):
        """EMA 对齐(+2) + RSI>60(+1) → score=3, 通过"""
        from cryptobot.realtime import monitor as mon_mod

        monkeypatch.setattr(
            mon_mod, "_load_5m_indicators",
            lambda s: {"rsi": 65, "ema7": 90, "ema25": 95},
        )
        assert mon_mod._confirm_indicators("BTCUSDT", "short") is True

    def test_short_oversold_extreme(self, monkeypatch):
        """EMA 对齐(+2) + RSI<=20(-3) → score=-1, 不通过"""
        from cryptobot.realtime import monitor as mon_mod

        monkeypatch.setattr(
            mon_mod, "_load_5m_indicators",
            lambda s: {"rsi": 15, "ema7": 90, "ema25": 95},
        )
        assert mon_mod._confirm_indicators("BTCUSDT", "short") is False

    def test_indicators_unavailable(self, monkeypatch):
        from cryptobot.realtime import monitor as mon_mod

        monkeypatch.setattr(mon_mod, "_load_5m_indicators", lambda s: None)
        assert mon_mod._confirm_indicators("BTCUSDT", "long") is True

    def test_scoring_long_ema_aligned_rsi_normal(self, monkeypatch):
        """评分制入场确认: EMA 对齐 + RSI 正常 → 通过"""
        from cryptobot.realtime import monitor as mon_mod

        monkeypatch.setattr(
            mon_mod, "_load_5m_indicators",
            lambda s: {"rsi": 50, "ema7": 100, "ema25": 99},
        )
        assert mon_mod._confirm_indicators("BTCUSDT", "long") is True

    def test_scoring_long_rsi_extreme_overbought(self, monkeypatch):
        """评分制入场确认: RSI 极端超买 → 不通过"""
        from cryptobot.realtime import monitor as mon_mod

        monkeypatch.setattr(
            mon_mod, "_load_5m_indicators",
            lambda s: {"rsi": 85, "ema7": 100, "ema25": 99},
        )
        assert mon_mod._confirm_indicators("BTCUSDT", "long") is False

    def test_scoring_long_ema_reversed_rsi_low(self, monkeypatch):
        """评分制入场确认: EMA 反向但 RSI 低 → 总分 -1+1=0 → 通过"""
        from cryptobot.realtime import monitor as mon_mod

        monkeypatch.setattr(
            mon_mod, "_load_5m_indicators",
            lambda s: {"rsi": 35, "ema7": 99, "ema25": 100},
        )
        assert mon_mod._confirm_indicators("BTCUSDT", "long") is True


# ─── TestPromoteSignal ───────────────────────────────────────────────────

class TestPromoteSignal:
    def test_writes_signal_and_removes_pending(self, signal_env):
        from cryptobot.realtime.monitor import _promote_signal

        sig = _make_pending_signal()
        write_pending_signal(sig)
        assert len(read_pending_signals()) == 1

        validated_sig = read_pending_signals()[0]
        _promote_signal(validated_sig)

        # pending 已移除
        assert len(read_pending_signals()) == 0

        # signal.json 已写入
        signal_file = signal_env / "signal.json"
        assert signal_file.exists()
        data = json.loads(signal_file.read_text())
        assert len(data["signals"]) == 1
        assert data["signals"][0]["symbol"] == "BTCUSDT"


# ─── TestRunMonitor ──────────────────────────────────────────────────────

class TestRunMonitor:
    def test_signal_activated(self, signal_env, mock_settings, monkeypatch):
        """价格进入区间 + 指标确认 → promote"""
        from cryptobot.realtime import monitor as mon_mod

        # 写入 pending 信号
        sig = _make_pending_signal(entry_range=[44000, 46000])
        write_pending_signal(sig)

        # mock 价格在入场区间内
        monkeypatch.setattr(mon_mod, "_fetch_price", lambda s: 45000.0)
        # mock 指标确认通过
        monkeypatch.setattr(mon_mod, "_confirm_indicators", lambda s, a: True)

        # 只运行一次循环
        call_count = 0

        def _patched_sleep(seconds):
            nonlocal call_count
            call_count += 1
            if call_count >= 2:
                raise KeyboardInterrupt

        import time
        monkeypatch.setattr(time, "sleep", _patched_sleep)

        mon_mod.run_monitor()

        # signal.json 应已写入
        signal_file = signal_env / "signal.json"
        assert signal_file.exists()
        data = json.loads(signal_file.read_text())
        assert len(data["signals"]) == 1
        assert data["signals"][0]["symbol"] == "BTCUSDT"

        # pending 应已清除
        assert len(read_pending_signals()) == 0

    def test_signal_expired(self, signal_env, mock_settings, monkeypatch):
        """超过 expires_at → 移除"""
        from cryptobot.realtime import monitor as mon_mod

        expired_time = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
        sig = _make_pending_signal(expires_at=expired_time)
        write_pending_signal(sig)

        call_count = 0

        def _patched_sleep(seconds):
            nonlocal call_count
            call_count += 1
            if call_count >= 2:
                raise KeyboardInterrupt

        import time
        monkeypatch.setattr(time, "sleep", _patched_sleep)
        monkeypatch.setattr(mon_mod, "_fetch_price", lambda s: 45000.0)

        mon_mod.run_monitor()

        # pending 应已移除（过期）
        assert len(read_pending_signals(filter_expired=False)) == 0

        # signal.json 不应存在（未 promote）
        assert not (signal_env / "signal.json").exists()

    def test_max_wait_timeout(self, signal_env, monkeypatch):
        """超过 max_wait_minutes → 移除"""
        from cryptobot.realtime import monitor as mon_mod

        # max_wait 设为 0 分钟，立即超时
        settings = {
            "realtime": {
                "poll_interval_seconds": 1,
                "price_tolerance_pct": 0.1,
                "require_indicator_confirm": False,
                "max_wait_minutes": 0,
            }
        }
        monkeypatch.setattr(mon_mod, "load_settings", lambda: settings)

        # 信号创建时间设为 1 分钟前
        old_time = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
        sig = _make_pending_signal(timestamp=old_time)
        write_pending_signal(sig)

        call_count = 0

        def _patched_sleep(seconds):
            nonlocal call_count
            call_count += 1
            if call_count >= 2:
                raise KeyboardInterrupt

        import time
        monkeypatch.setattr(time, "sleep", _patched_sleep)
        monkeypatch.setattr(mon_mod, "_fetch_price", lambda s: 45000.0)

        mon_mod.run_monitor()

        # pending 应已移除
        assert len(read_pending_signals(filter_expired=False)) == 0


# ─── TestRealtimeCLI ─────────────────────────────────────────────────────

class TestRealtimeCLI:
    def test_status_empty(self, signal_env):
        from click.testing import CliRunner
        from cryptobot.cli import cli

        runner = CliRunner()
        result = runner.invoke(cli, ["realtime", "status"])
        assert result.exit_code == 0
        assert "无 pending 信号" in result.output

    def test_status_with_signals(self, signal_env):
        from click.testing import CliRunner
        from cryptobot.cli import cli

        sig = _make_pending_signal()
        write_pending_signal(sig)

        runner = CliRunner()
        result = runner.invoke(cli, ["realtime", "status"])
        assert result.exit_code == 0
        assert "BTCUSDT" in result.output

    def test_status_json(self, signal_env):
        from click.testing import CliRunner
        from cryptobot.cli import cli

        sig = _make_pending_signal()
        write_pending_signal(sig)

        runner = CliRunner()
        result = runner.invoke(cli, ["realtime", "status", "--json-output"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert len(data) == 1
        assert data[0]["symbol"] == "BTCUSDT"


# ─── WS Price Feed ──────────────────────────────────────────────────────

class TestWSPriceFeed:
    def test_process_message_updates_cache(self):
        from cryptobot.realtime.ws_price_feed import _process_message, price_cache, _lock

        # 清空缓存
        with _lock:
            price_cache.clear()

        _process_message({"s": "BTCUSDT", "c": "95000.50"})
        from cryptobot.realtime.ws_price_feed import get_cached_price
        assert get_cached_price("BTCUSDT") == 95000.50

    def test_invalid_message_no_crash(self):
        from cryptobot.realtime.ws_price_feed import _process_message

        # 无 symbol
        _process_message({"c": "95000"})
        # 无 price
        _process_message({"s": "BTCUSDT"})
        # 空 dict
        _process_message({})
        # 非数字 price
        _process_message({"s": "BTCUSDT", "c": "not_a_number"})

    def test_cache_empty_rest_fallback(self, monkeypatch):
        """缓存为空时 _fetch_price 应 fallback REST"""
        from cryptobot.realtime import ws_price_feed as ws_mod
        from cryptobot.realtime.ws_price_feed import _lock

        # 清空缓存
        with _lock:
            ws_mod.price_cache.clear()

        # _fetch_price 应 fallback 到 REST
        from cryptobot.realtime.monitor import _fetch_price

        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = {"price": "42000.0"}

        import httpx
        monkeypatch.setattr(httpx, "get", lambda *a, **kw: mock_resp)

        price = _fetch_price("ETHUSDT")
        assert price == 42000.0

    def test_cache_hit_no_rest(self, monkeypatch):
        """缓存命中时不走 REST"""
        from cryptobot.realtime.ws_price_feed import _process_message, _lock, price_cache

        with _lock:
            price_cache.clear()

        _process_message({"s": "SOLUSDT", "c": "150.0"})

        from cryptobot.realtime.monitor import _fetch_price

        # 如果走了 REST 会抛异常
        import httpx
        monkeypatch.setattr(httpx, "get", lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("should not call REST")))

        price = _fetch_price("SOLUSDT")
        assert price == 150.0

    def test_get_all_cached_prices(self):
        from cryptobot.realtime.ws_price_feed import _process_message, get_all_cached_prices, _lock, price_cache

        with _lock:
            price_cache.clear()

        _process_message({"s": "BTCUSDT", "c": "95000"})
        _process_message({"s": "ETHUSDT", "c": "3500"})

        cached = get_all_cached_prices()
        assert cached["BTCUSDT"] == 95000.0
        assert cached["ETHUSDT"] == 3500.0
