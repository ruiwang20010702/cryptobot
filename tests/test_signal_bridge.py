"""信号桥接测试"""

import json
import sys

import pytest
from click.testing import CliRunner

from cryptobot.cli import cli

# 获取实际的 signal 模块 (避免与 click Group 名冲突)
_signal_mod = sys.modules["cryptobot.cli.signal"]


@pytest.fixture
def signal_dir(tmp_path, monkeypatch):
    """使用临时目录作为信号输出"""
    signal_path = tmp_path / "signals"
    signal_path.mkdir()
    monkeypatch.setattr(_signal_mod, "SIGNAL_DIR", signal_path)
    monkeypatch.setattr(_signal_mod, "SIGNAL_FILE", signal_path / "signal.json")
    return signal_path


class TestSignalWrite:
    def test_write_long_signal(self, signal_dir):
        runner = CliRunner()
        result = runner.invoke(
            cli,
            [
                "signal",
                "write",
                "--symbol",
                "BTCUSDT",
                "--action",
                "long",
                "--leverage",
                "3",
                "--amount",
                "1000",
                "--sl",
                "92000",
                "--tp",
                "105000",
            ],
        )
        assert result.exit_code == 0
        assert "信号已写入" in result.output

        signal_file = signal_dir / "signal.json"
        assert signal_file.exists()
        data = json.loads(signal_file.read_text())
        assert len(data["signals"]) == 1
        assert data["signals"][0]["symbol"] == "BTCUSDT"
        assert data["signals"][0]["action"] == "long"
        assert data["signals"][0]["leverage"] == 3

    def test_write_replaces_same_symbol(self, signal_dir):
        runner = CliRunner()
        runner.invoke(
            cli,
            ["signal", "write", "--symbol", "BTCUSDT", "--action", "long"],
        )
        runner.invoke(
            cli,
            ["signal", "write", "--symbol", "BTCUSDT", "--action", "short"],
        )

        data = json.loads((signal_dir / "signal.json").read_text())
        assert len(data["signals"]) == 1
        assert data["signals"][0]["action"] == "short"

    def test_write_multiple_symbols(self, signal_dir):
        runner = CliRunner()
        runner.invoke(cli, ["signal", "write", "--symbol", "BTCUSDT", "--action", "long"])
        runner.invoke(cli, ["signal", "write", "--symbol", "ETHUSDT", "--action", "short"])

        data = json.loads((signal_dir / "signal.json").read_text())
        assert len(data["signals"]) == 2


class TestSignalShow:
    def test_show_empty(self, signal_dir):
        runner = CliRunner()
        result = runner.invoke(cli, ["signal", "show"])
        assert result.exit_code == 0
        assert "无活跃信号" in result.output

    def test_show_with_signals(self, signal_dir):
        runner = CliRunner()
        runner.invoke(cli, ["signal", "write", "--symbol", "BTCUSDT", "--action", "long"])
        result = runner.invoke(cli, ["signal", "show"])
        assert result.exit_code == 0
        assert "BTCUSDT" in result.output


class TestSignalClear:
    def test_clear_specific_symbol(self, signal_dir):
        runner = CliRunner()
        runner.invoke(cli, ["signal", "write", "--symbol", "BTCUSDT", "--action", "long"])
        runner.invoke(cli, ["signal", "write", "--symbol", "ETHUSDT", "--action", "short"])
        result = runner.invoke(cli, ["signal", "clear", "--symbol", "BTCUSDT"])
        assert result.exit_code == 0
        assert "已清除 1 条" in result.output

        data = json.loads((signal_dir / "signal.json").read_text())
        assert len(data["signals"]) == 1
        assert data["signals"][0]["symbol"] == "ETHUSDT"


# ─── O5: regime 动态过期时间 ─────────────────────────────────────────────

class TestValidateSignalRegimeExpiry:
    def test_validate_signal_regime_expiry(self):
        """regime 动态过期时间"""
        from cryptobot.signal.bridge import validate_signal
        from datetime import datetime

        base = {"symbol": "BTCUSDT", "action": "long", "leverage": 3, "stop_loss": 90000}

        sig_trend = validate_signal({**base}, regime="trending")
        sig_range = validate_signal({**base}, regime="ranging")
        sig_volatile = validate_signal({**base}, regime="volatile")
        sig_default = validate_signal({**base})

        # 解析 expires_at
        t_trend = datetime.fromisoformat(sig_trend["expires_at"])
        t_range = datetime.fromisoformat(sig_range["expires_at"])
        t_volatile = datetime.fromisoformat(sig_volatile["expires_at"])
        t_default = datetime.fromisoformat(sig_default["expires_at"])

        # trending 过期最晚 (6h)，volatile 最早 (1.5h)
        assert t_trend > t_range
        assert t_range > t_volatile
        # default = 4h，应在 trending(6h) 和 ranging(2h) 之间
        assert t_trend > t_default > t_range
