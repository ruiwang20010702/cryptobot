"""Edge 仪表盘测试"""

import math
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest

from cryptobot.journal.edge import (
    EdgeMetrics,
    _calc_edge_ratio,
    _calc_expectancy,
    _calc_sqn,
    _r_to_bucket,
    calc_edge,
    detect_edge_decay,
)
from cryptobot.journal.models import SignalRecord


def _make_record(
    pnl: float,
    days_ago: int = 1,
    regime: str | None = None,
    stop_loss: float | None = None,
    entry_range: list | None = None,
) -> SignalRecord:
    """构造测试用 SignalRecord"""
    ts = (datetime.now(timezone.utc) - timedelta(days=days_ago)).isoformat()
    return SignalRecord(
        symbol="BTCUSDT",
        action="long",
        timestamp=ts,
        confidence=70,
        status="closed",
        actual_pnl_pct=pnl,
        stop_loss=stop_loss,
        entry_price_range=entry_range or [],
        regime_name=regime,
    )


class TestExpectancy:
    def test_basic(self):
        """手工验证: 3 wins(+2%) + 2 losses(-1%) -> wr=0.6, exp=0.6*2 - 0.4*1 = 0.8"""
        records = [
            _make_record(2.0),
            _make_record(2.0),
            _make_record(2.0),
            _make_record(-1.0),
            _make_record(-1.0),
        ]
        exp = _calc_expectancy(records)
        assert exp == pytest.approx(0.8, abs=0.001)

    def test_empty(self):
        assert _calc_expectancy([]) == 0.0

    def test_all_wins(self):
        records = [_make_record(3.0), _make_record(1.0)]
        # wr=1.0, avg_win=2.0, avg_loss=0 -> exp = 1.0*2.0 - 0*0 = 2.0
        assert _calc_expectancy(records) == pytest.approx(2.0, abs=0.001)

    def test_all_losses(self):
        records = [_make_record(-2.0), _make_record(-3.0)]
        # wr=0, avg_loss=2.5 -> exp = 0 - 1*2.5 = -2.5
        assert _calc_expectancy(records) == pytest.approx(-2.5, abs=0.001)


class TestEdgeRatio:
    def test_basic(self):
        """avg_win=3.0, avg_loss=1.5 -> ratio=2.0"""
        records = [
            _make_record(3.0),
            _make_record(3.0),
            _make_record(-1.5),
            _make_record(-1.5),
        ]
        assert _calc_edge_ratio(records) == pytest.approx(2.0, abs=0.001)

    def test_no_losses(self):
        records = [_make_record(2.0)]
        assert _calc_edge_ratio(records) == 0.0


class TestSQN:
    def test_formula(self):
        """SQN = sqrt(N) * mean / std, 手工验证"""
        records = [_make_record(2.0), _make_record(-1.0), _make_record(3.0)]
        pnl = [2.0, -1.0, 3.0]
        n = 3
        mean = sum(pnl) / n  # 4/3
        var = sum((x - mean) ** 2 for x in pnl) / (n - 1)
        std = math.sqrt(var)
        expected_sqn = math.sqrt(n) * mean / std
        assert _calc_sqn(records) == pytest.approx(expected_sqn, abs=0.001)

    def test_single_record(self):
        """单笔记录 -> sqn=0 (无法计算 std)"""
        records = [_make_record(2.0)]
        assert _calc_sqn(records) == 0.0

    def test_empty(self):
        assert _calc_sqn([]) == 0.0

    def test_identical_pnl(self):
        """所有 PnL 相同 -> std=0 -> sqn=0"""
        records = [_make_record(1.0), _make_record(1.0)]
        assert _calc_sqn(records) == 0.0


class TestRDistribution:
    def test_bucket_mapping(self):
        assert _r_to_bucket(-4.0) == "<-3R"
        assert _r_to_bucket(-2.5) == "-3R~-2R"
        assert _r_to_bucket(-1.5) == "-2R~-1R"
        assert _r_to_bucket(-0.5) == "-1R~0R"
        assert _r_to_bucket(0.5) == "0R~1R"
        assert _r_to_bucket(1.5) == "1R~2R"
        assert _r_to_bucket(2.5) == "2R~3R"
        assert _r_to_bucket(3.5) == ">3R"


class TestCalcEdge:
    def test_with_data(self):
        records = [
            _make_record(2.0, days_ago=1, regime="trending"),
            _make_record(-1.0, days_ago=2, regime="trending"),
            _make_record(3.0, days_ago=3, regime="ranging"),
            _make_record(-0.5, days_ago=5),
        ]
        with patch("cryptobot.journal.storage.get_all_records", return_value=records):
            metrics = calc_edge(30)

        assert isinstance(metrics, EdgeMetrics)
        assert metrics.expectancy_pct != 0
        assert isinstance(metrics.r_distribution, dict)
        assert "trending" in metrics.regime_edge
        assert "ranging" in metrics.regime_edge
        assert "recent_7d" in metrics.recent_vs_baseline
        assert "baseline_30d" in metrics.recent_vs_baseline
        assert "change" in metrics.recent_vs_baseline

    def test_empty_records(self):
        with patch("cryptobot.journal.storage.get_all_records", return_value=[]):
            metrics = calc_edge(30)

        assert metrics.expectancy_pct == 0.0
        assert metrics.edge_ratio == 0.0
        assert metrics.sqn == 0.0


class TestEdgeDecay:
    def test_decaying(self):
        """短期期望值为负，长期为正 -> decaying=True"""
        recent = [_make_record(-2.0, days_ago=1), _make_record(-1.0, days_ago=2)]
        old = [_make_record(3.0, days_ago=15), _make_record(2.0, days_ago=20)]
        all_records = recent + old

        with patch("cryptobot.journal.storage.get_all_records", return_value=all_records):
            result = detect_edge_decay(short_days=7, long_days=30)

        assert result["decaying"] is True
        assert result["short_expectancy"] < 0
        assert result["long_expectancy"] > 0
        assert result["warning"] != ""

    def test_not_decaying(self):
        """短期和长期都为正 -> decaying=False"""
        records = [
            _make_record(2.0, days_ago=1),
            _make_record(1.5, days_ago=3),
            _make_record(2.5, days_ago=15),
        ]
        with patch("cryptobot.journal.storage.get_all_records", return_value=records):
            result = detect_edge_decay(short_days=7, long_days=30)

        assert result["decaying"] is False
        assert result["warning"] == ""

    def test_empty_records(self):
        with patch("cryptobot.journal.storage.get_all_records", return_value=[]):
            result = detect_edge_decay()

        assert result["decaying"] is False
        assert result["change_pct"] == 0.0


class TestEdgeAPI:
    @pytest.fixture
    def client(self, monkeypatch):
        from fastapi.testclient import TestClient

        from cryptobot.web.app import create_app

        monkeypatch.setenv("DASHBOARD_TOKEN", "test-token")
        app = create_app()
        return TestClient(app, headers={"Authorization": "Bearer test-token"})

    def test_edge_endpoint(self, client):
        records = [
            _make_record(2.0, days_ago=1),
            _make_record(-1.0, days_ago=3),
            _make_record(1.5, days_ago=10),
        ]
        with patch("cryptobot.journal.storage.get_all_records", return_value=records):
            resp = client.get("/api/edge?days=30")

        assert resp.status_code == 200
        data = resp.json()
        assert "metrics" in data
        assert "decay" in data
        assert "expectancy_pct" in data["metrics"]
        assert "sqn" in data["metrics"]
        assert "r_distribution" in data["metrics"]
        assert "decaying" in data["decay"]

    def test_edge_page(self, client):
        resp = client.get("/edge")
        assert resp.status_code == 200
        assert "Edge Dashboard" in resp.text
