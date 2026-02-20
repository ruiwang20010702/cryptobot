"""Web Dashboard 测试

覆盖: API 路由、HTML 视图、CLI 命令
"""

from unittest.mock import patch, MagicMock

import pytest
from click.testing import CliRunner

from cryptobot.web.app import create_app


@pytest.fixture
def client():
    """FastAPI TestClient"""
    from fastapi.testclient import TestClient

    app = create_app()
    return TestClient(app)


# ─── API 路由 ─────────────────────────────────────────────────────────────

class TestDashboardAPI:
    @patch("cryptobot.freqtrade_api.ft_api_get")
    @patch("cryptobot.signal.bridge.read_signals", return_value=[])
    @patch("cryptobot.signal.bridge.read_pending_signals", return_value=[])
    @patch("cryptobot.journal.analytics.calc_performance")
    def test_dashboard(self, mock_perf, mock_pending, mock_signals, mock_ft, client):
        mock_ft.return_value = None
        mock_perf.return_value = {"closed": 0, "win_rate": 0, "total_signals": 0}

        resp = client.get("/api/dashboard")
        assert resp.status_code == 200
        data = resp.json()
        assert "account_balance" in data
        assert "positions" in data
        assert "signals" in data
        assert "performance" in data


class TestSignalsAPI:
    @patch("cryptobot.signal.bridge.read_signals", return_value=[{"symbol": "BTCUSDT"}])
    @patch("cryptobot.signal.bridge.read_pending_signals", return_value=[])
    def test_signals(self, mock_pending, mock_signals, client):
        resp = client.get("/api/signals")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["signals"]) == 1


class TestPositionsAPI:
    @patch("cryptobot.freqtrade_api.ft_api_get", return_value=[])
    def test_empty_positions(self, mock_ft, client):
        resp = client.get("/api/positions")
        assert resp.status_code == 200
        assert resp.json()["positions"] == []


class TestAlertsAPI:
    @patch("cryptobot.freqtrade_api.ft_api_get", return_value=None)
    @patch("cryptobot.signal.bridge.read_signals", return_value=[])
    def test_no_alerts(self, mock_signals, mock_ft, client):
        resp = client.get("/api/alerts")
        assert resp.status_code == 200
        assert "alerts" in resp.json()


class TestJournalStatsAPI:
    @patch("cryptobot.journal.analytics.calc_performance")
    def test_stats(self, mock_perf, client):
        mock_perf.return_value = {"closed": 5, "win_rate": 0.6}
        resp = client.get("/api/journal/stats")
        assert resp.status_code == 200
        assert resp.json()["win_rate"] == 0.6


class TestUpdateSignalAPI:
    @patch("cryptobot.signal.bridge.update_signal_field", return_value=True)
    def test_update(self, mock_update, client):
        resp = client.patch("/api/signals/BTCUSDT", json={"stop_loss": 90000})
        assert resp.status_code == 200
        assert resp.json()["results"]["stop_loss"] == "updated"

    def test_empty_updates(self, client):
        resp = client.patch("/api/signals/BTCUSDT", json={})
        assert resp.status_code == 400


# ─── HTML 视图 ────────────────────────────────────────────────────────────

class TestViews:
    def test_dashboard_page(self, client):
        resp = client.get("/")
        assert resp.status_code == 200
        assert "CryptoBot Dashboard" in resp.text


# ─── CLI ──────────────────────────────────────────────────────────────────

class TestWebCLI:
    def test_web_help(self):
        from cryptobot.cli.web import web

        runner = CliRunner()
        result = runner.invoke(web, ["--help"])
        assert result.exit_code == 0
        assert "Web Dashboard" in result.output

    def test_start_help(self):
        from cryptobot.cli.web import web

        runner = CliRunner()
        result = runner.invoke(web, ["start", "--help"])
        assert result.exit_code == 0
        assert "--host" in result.output
        assert "--port" in result.output
