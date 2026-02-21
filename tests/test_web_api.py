"""Web API 端点测试"""

from unittest.mock import patch

import pytest


@pytest.fixture
def client(monkeypatch):
    """创建 FastAPI 测试客户端（含认证 header）"""
    from fastapi.testclient import TestClient
    from cryptobot.web.app import create_app

    monkeypatch.setenv("DASHBOARD_TOKEN", "test-token")
    app = create_app()
    return TestClient(app, headers={"Authorization": "Bearer test-token"})


class TestKlinesAPI:
    def test_klines_success(self, client):
        import pandas as pd
        import numpy as np

        # 构造模拟 K 线数据
        dates = pd.date_range("2026-01-01", periods=10, freq="4h")
        df = pd.DataFrame({
            "open": np.random.uniform(90000, 100000, 10),
            "high": np.random.uniform(100000, 105000, 10),
            "low": np.random.uniform(85000, 90000, 10),
            "close": np.random.uniform(90000, 100000, 10),
            "volume": np.random.uniform(100, 1000, 10),
        }, index=dates)

        with patch("cryptobot.indicators.calculator.load_klines", return_value=df):
            resp = client.get("/api/klines/BTCUSDT?interval=4h&limit=5")
            assert resp.status_code == 200
            data = resp.json()
            assert data["symbol"] == "BTCUSDT"
            assert len(data["klines"]) == 5
            kline = data["klines"][0]
            assert "time" in kline
            assert "open" in kline
            assert "close" in kline

    def test_klines_not_found(self, client):
        with patch(
            "cryptobot.indicators.calculator.load_klines",
            side_effect=FileNotFoundError("No data"),
        ):
            resp = client.get("/api/klines/BTCUSDT")
            assert resp.status_code == 404

    def test_klines_invalid_symbol(self, client):
        """非法 symbol 返回 400"""
        resp = client.get("/api/klines/UNKNOWNUSDT")
        assert resp.status_code == 400


class TestJournalRecentAPI:
    def test_recent_empty(self, client):
        with patch("cryptobot.journal.storage.get_all_records", return_value=[]):
            resp = client.get("/api/journal/recent")
            assert resp.status_code == 200
            data = resp.json()
            assert data["records"] == []

    def test_recent_with_data(self, client):
        from cryptobot.journal.models import SignalRecord

        records = [
            SignalRecord(
                symbol="BTCUSDT", action="long",
                timestamp="2026-02-20T10:00:00+00:00",
                confidence=75, status="closed", actual_pnl_pct=2.5,
            ),
            SignalRecord(
                symbol="ETHUSDT", action="short",
                timestamp="2026-02-19T10:00:00+00:00",
                confidence=65, status="closed", actual_pnl_pct=-1.0,
            ),
        ]
        with patch("cryptobot.journal.storage.get_all_records", return_value=records):
            resp = client.get("/api/journal/recent?limit=1")
            assert resp.status_code == 200
            data = resp.json()
            assert len(data["records"]) == 1
            assert data["records"][0]["symbol"] == "BTCUSDT"
