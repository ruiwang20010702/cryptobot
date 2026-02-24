"""Telegram 命令处理器测试"""

from unittest.mock import patch, MagicMock
from dataclasses import dataclass

from cryptobot.telegram.handlers import handle_command, COMMANDS


class TestHandleCommand:
    def test_help_command(self):
        result = handle_command("/help")
        assert "CryptoBot" in result
        assert "/status" in result
        assert "/signals" in result

    def test_unknown_command(self):
        result = handle_command("/foobar")
        assert "未知命令" in result

    def test_all_commands_registered(self):
        expected = {
            "/help", "/status", "/signals", "/positions", "/alerts",
            "/pnl", "/edge", "/liq", "/weights", "/balance", "/risk",
        }
        assert set(COMMANDS.keys()) == expected

    def test_command_exception_returns_error(self):
        """命令异常时返回错误消息"""
        with patch.dict(COMMANDS, {"/crash": lambda: 1 / 0}):
            result = handle_command("/crash")
            assert "命令执行失败" in result


class TestCmdStatus:
    @patch("cryptobot.journal.analytics.calc_performance")
    @patch("cryptobot.signal.bridge.read_signals")
    def test_status(self, mock_signals, mock_perf):
        mock_signals.return_value = [{"symbol": "BTCUSDT"}, {"symbol": "ETHUSDT"}]
        mock_perf.return_value = {
            "closed": 10, "win_rate": 0.6, "avg_pnl_pct": 1.5,
        }
        result = handle_command("/status")
        assert "2" in result  # 2 active signals
        assert "60%" in result  # win rate


class TestCmdSignals:
    @patch("cryptobot.signal.bridge.read_signals")
    def test_no_signals(self, mock_signals):
        mock_signals.return_value = []
        result = handle_command("/signals")
        assert "无活跃信号" in result

    @patch("cryptobot.signal.bridge.read_signals")
    def test_with_signals(self, mock_signals):
        mock_signals.return_value = [{
            "symbol": "BTCUSDT",
            "action": "long",
            "leverage": 3,
            "entry_price_range": [94000, 96000],
            "stop_loss": 91000,
        }]
        result = handle_command("/signals")
        assert "BTCUSDT" in result
        assert "LONG" in result


class TestCmdPositions:
    @patch("cryptobot.telegram.handlers._get_virtual_positions", return_value=([], {}))
    @patch("cryptobot.freqtrade_api.ft_api_get")
    def test_no_positions(self, mock_ft, mock_vp):
        mock_ft.return_value = None
        result = handle_command("/positions")
        assert "无持仓" in result

    @patch("cryptobot.freqtrade_api.ft_api_get")
    def test_with_positions(self, mock_ft):
        mock_ft.return_value = [{
            "pair": "BTC/USDT:USDT",
            "is_short": False,
            "leverage": 3,
            "profit_ratio": 0.05,
        }]
        result = handle_command("/positions")
        assert "BTCUSDT" in result
        assert "LONG" in result

    @patch("cryptobot.telegram.handlers._get_virtual_positions")
    @patch("cryptobot.freqtrade_api.ft_api_get")
    def test_virtual_fallback(self, mock_ft, mock_vp):
        """Freqtrade 离线时 fallback 到虚拟盘"""
        from cryptobot.strategy.virtual_portfolio import VirtualPosition
        mock_ft.return_value = None
        pos = VirtualPosition(
            symbol="ETHUSDT", side="short", entry_price=3000.0,
            amount=0.5, leverage=5, opened_at="2026-02-01T00:00:00Z",
            strategy="funding_arb",
        )
        mock_vp.return_value = ([(pos, "funding_arb")], {"ETHUSDT": 2900.0})
        result = handle_command("/positions")
        assert "虚拟盘" in result
        assert "ETHUSDT" in result
        assert "SHORT" in result
        assert "funding_arb" in result


class TestCmdAlerts:
    @patch("cryptobot.cli.monitor._build_signal_only_alerts", return_value=[])
    @patch("cryptobot.freqtrade_api.ft_api_get", return_value=None)
    @patch("cryptobot.signal.bridge.read_signals", return_value=[])
    def test_no_alerts(self, mock_signals, mock_ft, mock_alerts):
        result = handle_command("/alerts")
        assert "无告警" in result


class TestCmdPnl:
    @patch("cryptobot.journal.analytics.calc_performance")
    def test_no_trades(self, mock_perf):
        mock_perf.return_value = {"closed": 0}
        result = handle_command("/pnl")
        assert "无已平仓" in result

    @patch("cryptobot.journal.analytics.calc_performance")
    def test_with_trades(self, mock_perf):
        mock_perf.return_value = {
            "closed": 15,
            "win_rate": 0.65,
            "avg_pnl_pct": 2.1,
            "profit_factor": 1.8,
            "total_pnl_usdt": 350,
            "by_direction": {
                "long": {"closed": 10, "win_rate": 0.7},
                "short": {"closed": 5, "win_rate": 0.6},
            },
        }
        result = handle_command("/pnl")
        assert "15 笔" in result
        assert "65%" in result


class TestCmdEdge:
    @patch("cryptobot.journal.edge.calc_edge")
    def test_edge(self, mock_edge):
        @dataclass
        class FakeEdge:
            expectancy_pct: float = 1.5
            edge_ratio: float = 2.0
            sqn: float = 1.8
            r_distribution: dict = None
            recent_vs_baseline: dict = None

        mock_edge.return_value = FakeEdge(r_distribution={}, recent_vs_baseline={})
        result = handle_command("/edge")
        assert "1.50%" in result
        assert "SQN" in result


class TestCmdLiq:
    @patch("cryptobot.telegram.handlers._get_virtual_positions", return_value=([], {}))
    @patch("cryptobot.freqtrade_api.ft_api_get")
    def test_no_positions(self, mock_ft, mock_vp):
        mock_ft.return_value = None
        result = handle_command("/liq")
        assert "无持仓" in result

    @patch("cryptobot.risk.liquidation_calc.full_liquidation_analysis")
    @patch("cryptobot.freqtrade_api.ft_api_get")
    def test_with_positions(self, mock_ft, mock_liq):
        mock_ft.return_value = [{
            "pair": "BTC/USDT:USDT",
            "is_short": False,
            "leverage": 5,
            "open_rate": 95000,
            "current_rate": 96000,
            "stake_amount": 200,
        }]
        mock_liq.return_value = {
            "distance_pct": 18.5,
            "liquidation_price": 77000,
            "risk_level": "warning",
        }
        result = handle_command("/liq")
        assert "BTCUSDT" in result
        assert "18.5%" in result

    @patch("cryptobot.telegram.handlers._get_virtual_positions")
    @patch("cryptobot.freqtrade_api.ft_api_get")
    def test_virtual_fallback(self, mock_ft, mock_vp):
        """Freqtrade 离线时 fallback 到虚拟盘爆仓计算"""
        from cryptobot.strategy.virtual_portfolio import VirtualPosition
        mock_ft.return_value = None
        pos = VirtualPosition(
            symbol="BTCUSDT", side="long", entry_price=95000.0,
            amount=0.01, leverage=5, opened_at="2026-02-01T00:00:00Z",
            strategy="grid",
        )
        mock_vp.return_value = ([(pos, "grid")], {"BTCUSDT": 96000.0})
        result = handle_command("/liq")
        assert "虚拟盘" in result
        assert "BTCUSDT" in result
        assert "LONG" in result
        assert "爆仓价" in result


class TestCmdWeights:
    @patch("cryptobot.strategy.weight_tracker.load_weights")
    def test_no_weights(self, mock_weights):
        mock_weights.return_value = None
        result = handle_command("/weights")
        assert "未配置" in result

    @patch("cryptobot.strategy.weight_tracker.load_weights")
    def test_with_weights(self, mock_weights):
        @dataclass
        class FakeWeight:
            strategy: str
            weight: float
            reason: str

        @dataclass
        class FakeAlloc:
            regime: str = "trending"
            weights: list = None
            updated_at: str = ""

        mock_weights.return_value = FakeAlloc(
            weights=[
                FakeWeight("ai_trend", 0.8, "趋势市主力"),
                FakeWeight("grid", 0.2, "网格辅助"),
            ],
        )
        result = handle_command("/weights")
        assert "trending" in result
        assert "ai_trend" in result


class TestCmdBalance:
    @patch("cryptobot.telegram.handlers._get_virtual_balance", return_value=(20000.0, 1000.0))
    @patch("cryptobot.freqtrade_api.ft_api_get")
    def test_virtual_fallback(self, mock_ft, mock_vb):
        """Freqtrade 离线时 fallback 到虚拟盘余额"""
        mock_ft.return_value = None
        result = handle_command("/balance")
        assert "虚拟盘" in result
        assert "20000.00" in result
        assert "1000.00" in result

    @patch("cryptobot.freqtrade_api.ft_api_get")
    def test_with_balance(self, mock_ft):
        mock_ft.return_value = {
            "total": 1000.50,
            "free": 500.25,
            "used": 500.25,
            "currencies": [],
        }
        result = handle_command("/balance")
        assert "1000.50" in result


class TestCmdRisk:
    @patch("cryptobot.risk.monthly_circuit_breaker.check_circuit_breaker")
    def test_normal(self, mock_cb):
        @dataclass
        class FakeCB:
            consecutive_loss_months: int = 0
            action: str = "normal"
            position_scale: float = 1.0
            block_long: bool = False
            resume_date: str = None
            reason: str = "无连续亏损"

        mock_cb.return_value = FakeCB()
        result = handle_command("/risk")
        assert "normal" in result
        assert "100%" in result
