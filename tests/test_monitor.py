"""监控命令测试"""

import json
import sys
from datetime import datetime, timezone, timedelta
from unittest.mock import patch

import pytest
from click.testing import CliRunner

from cryptobot.cli import cli

# 获取实际的 monitor 模块 (避免与 click Group 名冲突)
_monitor_mod = sys.modules["cryptobot.cli.monitor"]


@pytest.fixture
def mock_ft_offline():
    """模拟 Freqtrade 未运行"""
    with patch.object(_monitor_mod, "ft_api_get", return_value=None):
        yield


@pytest.fixture
def mock_ft_no_positions():
    """模拟 Freqtrade 运行但无持仓"""
    def _ft_get(endpoint):
        if endpoint == "/status":
            return []
        if endpoint == "/profit":
            return {"profit_all_coin": 0, "profit_all_pct": 0, "trade_count": 0,
                    "winning_trades": 0, "losing_trades": 0}
        if endpoint == "/balance":
            return {"currencies": [{"currency": "USDT", "balance": 10000, "free": 10000, "used": 0}]}
        return {}

    with patch.object(_monitor_mod, "ft_api_get", side_effect=_ft_get):
        yield


@pytest.fixture
def mock_ft_with_positions():
    """模拟 Freqtrade 有持仓"""

    def _ft_get(endpoint):
        if endpoint == "/status":
            return [
                {
                    "pair": "BTC/USDT:USDT",
                    "is_short": False,
                    "leverage": 3,
                    "open_rate": 95000,
                    "current_rate": 97000,
                    "profit_pct": 0.063,
                    "profit_abs": 63.0,
                    "stop_loss_abs": 92000,
                    "trade_duration": "2h",
                },
                {
                    "pair": "ETH/USDT:USDT",
                    "is_short": True,
                    "leverage": 2,
                    "open_rate": 3500,
                    "current_rate": 3600,
                    "profit_pct": -0.057,
                    "profit_abs": -28.5,
                    "stop_loss_abs": 3700,
                    "trade_duration": "1h",
                },
            ]
        if endpoint == "/profit":
            return {"profit_all_coin": 34.5, "profit_all_pct": 0.35, "trade_count": 5,
                    "winning_trades": 3, "losing_trades": 2}
        if endpoint == "/balance":
            return {"currencies": [{"currency": "USDT", "balance": 10034.5, "free": 8000, "used": 2034.5}]}
        return {}

    with patch.object(_monitor_mod, "ft_api_get", side_effect=_ft_get):
        yield


@pytest.fixture
def mock_signals(tmp_path, monkeypatch):
    """写入测试信号"""
    import cryptobot.signal.bridge as bridge_mod

    signal_dir = tmp_path / "signals"
    signal_dir.mkdir()
    monkeypatch.setattr(bridge_mod, "SIGNAL_DIR", signal_dir)
    monkeypatch.setattr(bridge_mod, "SIGNAL_FILE", signal_dir / "signal.json")

    now = datetime.now(timezone.utc)
    data = {
        "signals": [
            {
                "symbol": "BTCUSDT",
                "action": "long",
                "leverage": 3,
                "position_size_usdt": 1000,
                "stop_loss": 92000,
                "take_profit": [{"price": 105000, "close_pct": 100}],
                "confidence": 75,
                "expires_at": (now + timedelta(hours=4)).isoformat(),
                "timestamp": now.isoformat(),
            }
        ],
        "last_updated": now.isoformat(),
    }
    (signal_dir / "signal.json").write_text(json.dumps(data))
    return signal_dir


class TestCheckAlerts:
    def test_ft_offline_no_signals(self, mock_ft_offline, tmp_path, monkeypatch):
        import cryptobot.signal.bridge as bridge_mod
        monkeypatch.setattr(bridge_mod, "SIGNAL_FILE", tmp_path / "nonexistent.json")

        runner = CliRunner()
        result = runner.invoke(cli, ["monitor", "check-alerts"])
        assert result.exit_code == 0
        assert "无告警" in result.output or "Freqtrade 未运行" in result.output

    def test_ft_offline_json(self, mock_ft_offline, tmp_path, monkeypatch):
        import cryptobot.signal.bridge as bridge_mod
        monkeypatch.setattr(bridge_mod, "SIGNAL_FILE", tmp_path / "nonexistent.json")

        runner = CliRunner()
        result = runner.invoke(cli, ["monitor", "check-alerts", "--json-output"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["freqtrade_connected"] is False
        assert "alerts" in data

    def test_ft_with_positions_json(self, mock_ft_with_positions, mock_signals):
        runner = CliRunner()
        result = runner.invoke(cli, ["monitor", "check-alerts", "--json-output"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["freqtrade_connected"] is True
        assert data["active_positions"] == 2

    def test_ft_no_positions(self, mock_ft_no_positions, mock_signals):
        runner = CliRunner()
        result = runner.invoke(cli, ["monitor", "check-alerts"])
        assert result.exit_code == 0
        assert "无告警" in result.output


class TestLiquidationDistance:
    def test_ft_with_positions(self, mock_ft_with_positions, mock_signals):
        runner = CliRunner()
        result = runner.invoke(cli, ["monitor", "liquidation-distance"])
        assert result.exit_code == 0
        assert "BTC" in result.output
        assert "ETH" in result.output

    def test_ft_with_positions_json(self, mock_ft_with_positions, mock_signals):
        runner = CliRunner()
        result = runner.invoke(cli, ["monitor", "liquidation-distance", "--json-output"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert len(data) == 2
        assert data[0]["symbol"] == "BTCUSDT"
        assert "distance_pct" in data[0]
        assert "risk_level" in data[0]

    def test_no_positions(self, mock_ft_offline, tmp_path, monkeypatch):
        import cryptobot.signal.bridge as bridge_mod
        monkeypatch.setattr(bridge_mod, "SIGNAL_FILE", tmp_path / "nonexistent.json")

        runner = CliRunner()
        result = runner.invoke(cli, ["monitor", "liquidation-distance"])
        assert result.exit_code == 0
        assert "无持仓" in result.output


class TestDailyReport:
    def test_ft_offline(self, mock_ft_offline, tmp_path, monkeypatch):
        import cryptobot.signal.bridge as bridge_mod
        monkeypatch.setattr(bridge_mod, "SIGNAL_FILE", tmp_path / "nonexistent.json")

        runner = CliRunner()
        result = runner.invoke(cli, ["monitor", "daily-report"])
        assert result.exit_code == 0
        assert "未连接" in result.output

    def test_ft_with_positions(self, mock_ft_with_positions, mock_signals):
        runner = CliRunner()
        result = runner.invoke(cli, ["monitor", "daily-report"])
        assert result.exit_code == 0
        assert "已连接" in result.output

    def test_ft_with_positions_json(self, mock_ft_with_positions, mock_signals):
        runner = CliRunner()
        result = runner.invoke(cli, ["monitor", "daily-report", "--json-output"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["freqtrade_connected"] is True
        assert data["portfolio"]["open_positions"] == 2
        assert "alerts" in data
        assert "summary" in data

    def test_report_has_signal_details(self, mock_ft_offline, mock_signals):
        runner = CliRunner()
        result = runner.invoke(cli, ["monitor", "daily-report", "--json-output"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["signals"]["active"] == 1
        assert data["signals"]["details"][0]["symbol"] == "BTCUSDT"
