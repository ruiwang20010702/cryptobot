"""月度亏损熔断模块测试"""

from datetime import datetime, timezone
from unittest.mock import patch

from cryptobot.risk.monthly_circuit_breaker import (
    MonthlyPnL,
    calc_monthly_pnl,
    check_circuit_breaker,
)


def _make_record(year_month: str, pnl: float, day: int = 15):
    """构造一个 closed 记录 stub"""
    from cryptobot.journal.models import SignalRecord

    return SignalRecord(
        symbol="BTCUSDT",
        action="long",
        status="closed",
        actual_pnl_usdt=pnl,
        timestamp=f"{year_month}-{day:02d}T12:00:00+00:00",
    )


_DEFAULT_SETTINGS = {
    "risk": {
        "circuit_breaker": {
            "enabled": True,
            "reduce_after_months": 2,
            "suspend_after_months": 3,
            "suspend_days": 7,
            "position_scale": 0.5,
        }
    }
}


class TestCalcMonthlyPnl:
    @patch("cryptobot.risk.monthly_circuit_breaker.datetime")
    @patch("cryptobot.journal.storage.get_all_records")
    def test_basic_grouping(self, mock_records, mock_dt):
        mock_dt.now.return_value = datetime(2026, 3, 15, tzinfo=timezone.utc)
        mock_dt.fromisoformat = datetime.fromisoformat
        mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)

        mock_records.return_value = [
            _make_record("2026-02", 100.0),
            _make_record("2026-02", -50.0),
            _make_record("2026-01", -200.0),
        ]

        result = calc_monthly_pnl(months=3)
        assert len(result) >= 2

        feb = next((m for m in result if m.year_month == "2026-02"), None)
        assert feb is not None
        assert feb.pnl_usdt == 50.0
        assert feb.trade_count == 2

        jan = next((m for m in result if m.year_month == "2026-01"), None)
        assert jan is not None
        assert jan.pnl_usdt == -200.0
        assert jan.trade_count == 1

    @patch("cryptobot.journal.storage.get_all_records")
    def test_empty_records(self, mock_records):
        mock_records.return_value = []
        result = calc_monthly_pnl(months=3)
        assert isinstance(result, list)
        for m in result:
            assert m.trade_count == 0
            assert m.pnl_usdt == 0.0

    @patch("cryptobot.journal.storage.get_all_records")
    def test_skips_non_closed(self, mock_records):
        from cryptobot.journal.models import SignalRecord

        mock_records.return_value = [
            SignalRecord(
                symbol="BTCUSDT",
                action="long",
                status="active",
                actual_pnl_usdt=500.0,
                timestamp="2026-02-10T12:00:00+00:00",
            ),
            SignalRecord(
                symbol="BTCUSDT",
                action="long",
                status="closed",
                actual_pnl_usdt=None,
                timestamp="2026-02-10T12:00:00+00:00",
            ),
        ]
        result = calc_monthly_pnl(months=3)
        for m in result:
            assert m.trade_count == 0


class TestCheckCircuitBreaker:
    @patch("cryptobot.risk.monthly_circuit_breaker.calc_monthly_pnl")
    @patch("cryptobot.risk.monthly_circuit_breaker._load_cb_config")
    @patch("cryptobot.risk.monthly_circuit_breaker.datetime")
    def test_normal_state(self, mock_dt, mock_cfg, mock_pnl):
        mock_dt.now.return_value = datetime(2026, 3, 15, tzinfo=timezone.utc)
        mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
        mock_cfg.return_value = _DEFAULT_SETTINGS["risk"]["circuit_breaker"]

        mock_pnl.return_value = [
            MonthlyPnL("2026-02", 100.0, 0.0, 5),
            MonthlyPnL("2026-01", 50.0, 0.0, 3),
        ]

        state = check_circuit_breaker()
        assert state.action == "normal"
        assert state.position_scale == 1.0
        assert state.block_long is False
        assert state.consecutive_loss_months == 0

    @patch("cryptobot.risk.monthly_circuit_breaker.calc_monthly_pnl")
    @patch("cryptobot.risk.monthly_circuit_breaker._load_cb_config")
    @patch("cryptobot.risk.monthly_circuit_breaker.datetime")
    def test_reduce_after_2_months(self, mock_dt, mock_cfg, mock_pnl):
        mock_dt.now.return_value = datetime(2026, 3, 15, tzinfo=timezone.utc)
        mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
        mock_cfg.return_value = _DEFAULT_SETTINGS["risk"]["circuit_breaker"]

        mock_pnl.return_value = [
            MonthlyPnL("2026-02", -100.0, 0.0, 5),
            MonthlyPnL("2026-01", -50.0, 0.0, 3),
            MonthlyPnL("2025-12", 200.0, 0.0, 4),
        ]

        state = check_circuit_breaker()
        assert state.action == "reduce"
        assert state.position_scale == 0.5
        assert state.block_long is True
        assert state.consecutive_loss_months == 2
        assert state.resume_date is None

    @patch("cryptobot.risk.monthly_circuit_breaker.calc_monthly_pnl")
    @patch("cryptobot.risk.monthly_circuit_breaker._load_cb_config")
    @patch("cryptobot.risk.monthly_circuit_breaker.datetime")
    def test_suspend_after_3_months(self, mock_dt, mock_cfg, mock_pnl):
        mock_dt.now.return_value = datetime(2026, 3, 15, tzinfo=timezone.utc)
        mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
        mock_cfg.return_value = _DEFAULT_SETTINGS["risk"]["circuit_breaker"]

        mock_pnl.return_value = [
            MonthlyPnL("2026-02", -80.0, 0.0, 4),
            MonthlyPnL("2026-01", -120.0, 0.0, 6),
            MonthlyPnL("2025-12", -30.0, 0.0, 2),
            MonthlyPnL("2025-11", 50.0, 0.0, 3),
        ]

        state = check_circuit_breaker()
        assert state.action == "suspend"
        assert state.position_scale == 0.0
        assert state.block_long is True
        assert state.consecutive_loss_months == 3
        assert state.resume_date is not None

    @patch("cryptobot.risk.monthly_circuit_breaker.calc_monthly_pnl")
    @patch("cryptobot.risk.monthly_circuit_breaker._load_cb_config")
    @patch("cryptobot.risk.monthly_circuit_breaker.datetime")
    def test_disabled(self, mock_dt, mock_cfg, mock_pnl):
        mock_dt.now.return_value = datetime(2026, 3, 15, tzinfo=timezone.utc)
        mock_cfg.return_value = {
            "enabled": False,
            "reduce_after_months": 2,
            "suspend_after_months": 3,
            "suspend_days": 7,
            "position_scale": 0.5,
        }

        state = check_circuit_breaker()
        assert state.action == "normal"
        assert state.position_scale == 1.0
        mock_pnl.assert_not_called()

    @patch("cryptobot.risk.monthly_circuit_breaker.calc_monthly_pnl")
    @patch("cryptobot.risk.monthly_circuit_breaker._load_cb_config")
    @patch("cryptobot.risk.monthly_circuit_breaker.datetime")
    def test_no_completed_months(self, mock_dt, mock_cfg, mock_pnl):
        """当月无已完结月份时返回 normal"""
        mock_dt.now.return_value = datetime(2026, 3, 15, tzinfo=timezone.utc)
        mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
        mock_cfg.return_value = _DEFAULT_SETTINGS["risk"]["circuit_breaker"]

        # 只有当月数据
        mock_pnl.return_value = [
            MonthlyPnL("2026-03", -500.0, 0.0, 10),
        ]

        state = check_circuit_breaker()
        assert state.action == "normal"
        assert state.consecutive_loss_months == 0

    @patch("cryptobot.risk.monthly_circuit_breaker.calc_monthly_pnl")
    @patch("cryptobot.risk.monthly_circuit_breaker._load_cb_config")
    @patch("cryptobot.risk.monthly_circuit_breaker.datetime")
    def test_zero_trades_breaks_streak(self, mock_dt, mock_cfg, mock_pnl):
        """无交易月份中断连续亏损计数"""
        mock_dt.now.return_value = datetime(2026, 3, 15, tzinfo=timezone.utc)
        mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
        mock_cfg.return_value = _DEFAULT_SETTINGS["risk"]["circuit_breaker"]

        mock_pnl.return_value = [
            MonthlyPnL("2026-02", -100.0, 0.0, 3),
            MonthlyPnL("2026-01", 0.0, 0.0, 0),  # 无交易
            MonthlyPnL("2025-12", -200.0, 0.0, 5),
        ]

        state = check_circuit_breaker()
        assert state.consecutive_loss_months == 1
        assert state.action == "normal"
