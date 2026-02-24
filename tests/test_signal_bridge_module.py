"""信号桥接模块测试"""


import pytest

from cryptobot.signal.bridge import (
    validate_signal,
    write_signal,
    read_signals,
    get_signal_for_pair,
    cleanup_expired,
    write_pending_signal,
    read_pending_signals,
    remove_pending_signal,
    update_signal_field,
)


@pytest.fixture
def signal_env(tmp_path, monkeypatch):
    """使用临时目录"""
    import cryptobot.signal.bridge as mod
    sig_dir = tmp_path / "signals"
    sig_dir.mkdir()
    monkeypatch.setattr(mod, "SIGNAL_DIR", sig_dir)
    monkeypatch.setattr(mod, "SIGNAL_FILE", sig_dir / "signal.json")
    monkeypatch.setattr(mod, "PENDING_FILE", sig_dir / "pending_signals.json")
    return sig_dir


class TestValidateSignal:
    def test_minimal_valid(self):
        s = validate_signal({"symbol": "BTCUSDT", "action": "long", "stop_loss": 90000})
        assert s["symbol"] == "BTCUSDT"
        assert s["action"] == "long"
        assert s["leverage"] == 3  # 默认值
        assert s["confidence"] == 50

    def test_missing_symbol_raises(self):
        with pytest.raises(ValueError, match="symbol"):
            validate_signal({"action": "long"})

    def test_invalid_action_raises(self):
        with pytest.raises(ValueError, match="action"):
            validate_signal({"symbol": "BTCUSDT", "action": "buy"})

    def test_excessive_leverage_raises(self):
        with pytest.raises(ValueError, match="杠杆"):
            validate_signal({"symbol": "BTCUSDT", "action": "long", "leverage": 10})

    def test_long_sl_above_entry_raises(self):
        with pytest.raises(ValueError, match="多单止损"):
            validate_signal({
                "symbol": "BTCUSDT",
                "action": "long",
                "stop_loss": 105_000,
                "entry_price_range": [100_000, 101_000],
            })

    def test_short_sl_below_entry_raises(self):
        with pytest.raises(ValueError, match="空单止损"):
            validate_signal({
                "symbol": "BTCUSDT",
                "action": "short",
                "stop_loss": 95_000,
                "entry_price_range": [100_000, 101_000],
            })


class TestWriteReadSignals:
    def test_write_and_read(self, signal_env):
        write_signal({"symbol": "BTCUSDT", "action": "long", "confidence": 80, "stop_loss": 90000})
        signals = read_signals()
        assert len(signals) == 1
        assert signals[0]["symbol"] == "BTCUSDT"
        assert signals[0]["confidence"] == 80

    def test_get_signal_for_pair(self, signal_env):
        write_signal({"symbol": "BTCUSDT", "action": "long", "stop_loss": 90000})
        write_signal({"symbol": "ETHUSDT", "action": "short", "stop_loss": 3500})

        s = get_signal_for_pair("BTC/USDT:USDT")
        assert s is not None
        assert s["action"] == "long"

        s = get_signal_for_pair("ETHUSDT")
        assert s is not None
        assert s["action"] == "short"

        s = get_signal_for_pair("FAKEUSDT")
        assert s is None

    def test_replace_same_symbol(self, signal_env):
        write_signal({"symbol": "BTCUSDT", "action": "long", "stop_loss": 90000})
        write_signal({"symbol": "BTCUSDT", "action": "short", "stop_loss": 110000})
        signals = read_signals()
        assert len(signals) == 1
        assert signals[0]["action"] == "short"


class TestCleanup:
    def test_cleanup_expired(self, signal_env):
        # 写入一个已过期信号
        write_signal({
            "symbol": "BTCUSDT",
            "action": "long",
            "stop_loss": 90000,
            "expires_at": "2020-01-01T00:00:00+00:00",
        })
        # 写入一个有效信号
        write_signal({"symbol": "ETHUSDT", "action": "short", "stop_loss": 3500})

        expired = cleanup_expired()
        assert len(expired) == 1
        assert expired[0]["symbol"] == "BTCUSDT"
        signals = read_signals(filter_expired=False)
        assert len(signals) == 1
        assert signals[0]["symbol"] == "ETHUSDT"


class TestPendingSignals:
    def test_write_and_read_pending(self, signal_env):
        write_pending_signal({"symbol": "BTCUSDT", "action": "long", "confidence": 80, "stop_loss": 90000})
        pending = read_pending_signals()
        assert len(pending) == 1
        assert pending[0]["symbol"] == "BTCUSDT"
        assert pending[0]["confidence"] == 80

    def test_replace_same_symbol(self, signal_env):
        write_pending_signal({"symbol": "BTCUSDT", "action": "long", "stop_loss": 90000})
        write_pending_signal({"symbol": "BTCUSDT", "action": "short", "stop_loss": 110000})
        pending = read_pending_signals()
        assert len(pending) == 1
        assert pending[0]["action"] == "short"

    def test_remove_pending(self, signal_env):
        write_pending_signal({"symbol": "BTCUSDT", "action": "long", "stop_loss": 90000})
        write_pending_signal({"symbol": "ETHUSDT", "action": "short", "stop_loss": 3500})

        result = remove_pending_signal("BTCUSDT")
        assert result is True
        pending = read_pending_signals()
        assert len(pending) == 1
        assert pending[0]["symbol"] == "ETHUSDT"

    def test_remove_nonexistent(self, signal_env):
        write_pending_signal({"symbol": "BTCUSDT", "action": "long", "stop_loss": 90000})
        result = remove_pending_signal("FAKEUSDT")
        assert result is False

    def test_filter_expired(self, signal_env):
        # 写入一个已过期的 pending 信号
        write_pending_signal({
            "symbol": "BTCUSDT",
            "action": "long",
            "stop_loss": 90000,
            "expires_at": "2020-01-01T00:00:00+00:00",
        })
        # 写入一个有效的 pending 信号
        write_pending_signal({"symbol": "ETHUSDT", "action": "short", "stop_loss": 3500})

        # 过滤过期信号
        pending = read_pending_signals(filter_expired=True)
        assert len(pending) == 1
        assert pending[0]["symbol"] == "ETHUSDT"

        # 不过滤
        all_pending = read_pending_signals(filter_expired=False)
        assert len(all_pending) == 2


class TestUpdateSignalField:
    def test_update_stop_loss(self, signal_env):
        write_signal({"symbol": "BTCUSDT", "action": "long", "stop_loss": 42000})
        result = update_signal_field("BTCUSDT", "stop_loss", 43000)
        assert result is True

        signals = read_signals()
        assert signals[0]["stop_loss"] == 43000

    def test_update_nonexistent_symbol(self, signal_env):
        write_signal({"symbol": "BTCUSDT", "action": "long", "stop_loss": 90000})
        result = update_signal_field("FAKEUSDT", "stop_loss", 42000)
        assert result is False

    def test_update_no_signal_file(self, signal_env):
        result = update_signal_field("BTCUSDT", "stop_loss", 42000)
        assert result is False
