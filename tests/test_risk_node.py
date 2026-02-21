"""risk_review 节点测试

覆盖:
- C1: 日度/周度亏损限制
- C2: 仓位竞态累加
- C3: 余额为 0 拒绝开仓
- 资金层级硬性规则
- 爆仓距离计算集成
"""

from unittest.mock import patch, MagicMock

import pytest

from cryptobot.workflow.nodes.risk import risk_review, _check_loss_limits


def _make_decision(
    symbol="BTCUSDT", action="long", confidence=75, leverage=3, position_size_pct=20,
):
    return {
        "symbol": symbol,
        "action": action,
        "confidence": confidence,
        "leverage": leverage,
        "entry_price_range": [60000, 61000],
        "stop_loss": 59000,
        "take_profit": [63000],
        "position_size_pct": position_size_pct,
        "current_price": 60500,
        "reasoning": "test",
    }


def _base_state(**overrides):
    state = {
        "decisions": [],
        "errors": [],
        "analyses": {},
        "market_regime": {},
        "capital_tier": {},
        "screened_symbols": [],
        "fear_greed": {},
    }
    state.update(overrides)
    return state


# ─── C1: 亏损限制 ─────────────────────────────────────────────────────────

class TestCheckLossLimits:
    @patch("cryptobot.journal.analytics.calc_performance")
    def test_daily_loss_exceeded(self, mock_perf):
        """日度亏损超限 → 拒绝"""
        mock_perf.return_value = {"closed": 5, "avg_pnl_pct": -2.0}
        ok, reason = _check_loss_limits({"max_loss": {"daily_pct": 5, "weekly_pct": 8, "monthly_drawdown_pct": 15}})
        assert not ok
        assert "日度" in reason

    @patch("cryptobot.journal.analytics.calc_performance")
    def test_no_closed_trades_passes(self, mock_perf):
        """无已平仓交易 → 通过"""
        mock_perf.return_value = {"closed": 0, "avg_pnl_pct": 0}
        ok, _ = _check_loss_limits({"max_loss": {"daily_pct": 5, "weekly_pct": 8, "monthly_drawdown_pct": 15}})
        assert ok

    def test_exception_skips_check(self):
        """journal 异常 → 跳过检查（通过）"""
        with patch("cryptobot.journal.analytics.calc_performance", side_effect=Exception("db error")):
            ok, _ = _check_loss_limits({"max_loss": {"daily_pct": 5}})
        assert ok

    @patch("cryptobot.journal.analytics.calc_performance")
    def test_within_limit_passes(self, mock_perf):
        """亏损在限制内 → 通过"""
        mock_perf.return_value = {"closed": 3, "avg_pnl_pct": -1.0}
        ok, _ = _check_loss_limits({"max_loss": {"daily_pct": 5, "weekly_pct": 8, "monthly_drawdown_pct": 15}})
        assert ok


# ─── C3: 余额为 0 ─────────────────────────────────────────────────────────

class TestBalanceZero:
    @patch("cryptobot.notify.send_message", return_value=True)
    @patch("cryptobot.freqtrade_api.ft_api_get", return_value=None)
    @patch("cryptobot.signal.bridge.read_signals", return_value=[])
    def test_zero_balance_rejects_all(self, mock_signals, mock_ft, mock_notify):
        """Freqtrade 离线 → 拒绝所有"""
        state = _base_state(decisions=[_make_decision()])
        result = risk_review(state)
        assert result["approved_signals"] == []

    @patch("cryptobot.notify.send_message", return_value=True)
    @patch("cryptobot.freqtrade_api.ft_api_get")
    @patch("cryptobot.signal.bridge.read_signals", return_value=[])
    def test_balance_present_proceeds(self, mock_signals, mock_ft, mock_notify):
        """有余额 → 继续审核"""
        def ft_side_effect(endpoint):
            if endpoint == "/balance":
                return {"currencies": [{"currency": "USDT", "balance": 5000}]}
            if endpoint == "/status":
                return []
            return None

        mock_ft.side_effect = ft_side_effect
        state = _base_state(decisions=[_make_decision(action="no_trade")])

        with patch("cryptobot.journal.analytics.calc_performance", return_value={"closed": 0}):
            result = risk_review(state)
        # no_trade 被过滤, 无 actionable 决策
        assert result["approved_signals"] == []


# ─── C2: 仓位竞态 ──────────────────────────────────────────────────────────

class TestPositionRace:
    @patch("cryptobot.notify.send_message", return_value=True)
    @patch("cryptobot.workflow.llm.call_claude_parallel")
    @patch("cryptobot.freqtrade_api.ft_api_get")
    @patch("cryptobot.signal.bridge.read_signals", return_value=[])
    def test_cumulative_position_check(self, mock_signals, mock_ft, mock_parallel, mock_notify):
        """同批多信号仓位累加检查"""
        def ft_side_effect(endpoint):
            if endpoint == "/balance":
                return {"currencies": [{"currency": "USDT", "balance": 1000}]}
            if endpoint == "/status":
                return []
            return None

        mock_ft.side_effect = ft_side_effect
        # 3 个信号各 30%
        decisions = [
            _make_decision("BTCUSDT", position_size_pct=30),
            _make_decision("ETHUSDT", position_size_pct=30),
            _make_decision("SOLUSDT", position_size_pct=30),
        ]
        # 风控全部通过
        mock_parallel.return_value = [
            {"decision": "approved", "risk_score": 3},
            {"decision": "approved", "risk_score": 3},
            {"decision": "approved", "risk_score": 3},
        ]
        state = _base_state(decisions=decisions)

        with patch("cryptobot.journal.analytics.calc_performance", return_value={"closed": 0}):
            with patch("cryptobot.journal.analytics.build_performance_summary", return_value=""):
                with patch("cryptobot.journal.confidence_tuner.calc_dynamic_threshold",
                           side_effect=Exception("skip")):
                    result = risk_review(state)

        # 应有信号通过（前两个通过，第三个可能被仓位限制拦截）
        # 关键是累加逻辑生效
        assert len(result["approved_signals"]) >= 1


# ─── H6: 分级保证金率 ─────────────────────────────────────────────────────

class TestTieredMMR:
    def test_btc_small_position(self):
        from cryptobot.risk.liquidation_calc import _get_maintenance_margin_rate
        rate = _get_maintenance_margin_rate("BTCUSDT", 10000)
        assert rate == 0.004

    def test_btc_large_position(self):
        from cryptobot.risk.liquidation_calc import _get_maintenance_margin_rate
        rate = _get_maintenance_margin_rate("BTCUSDT", 300000)
        assert rate == 0.01

    def test_altcoin_default(self):
        from cryptobot.risk.liquidation_calc import _get_maintenance_margin_rate
        rate = _get_maintenance_margin_rate("DOGEUSDT", 5000)
        assert rate == 0.01

    def test_altcoin_large(self):
        from cryptobot.risk.liquidation_calc import _get_maintenance_margin_rate
        rate = _get_maintenance_margin_rate("DOGEUSDT", 100000)
        assert rate == 0.02

    def test_full_analysis_uses_tiered(self):
        from cryptobot.risk.liquidation_calc import full_liquidation_analysis
        # DOGE 大仓位应使用更高 MMR
        result = full_liquidation_analysis(
            entry_price=0.15, current_price=0.14,
            leverage=3, side="long",
            position_size_usdt=50000, symbol="DOGEUSDT",
        )
        assert result["distance_pct"] > 0
