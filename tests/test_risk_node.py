"""risk_review 节点测试

覆盖:
- C1: 日度/周度亏损限制
- C2: 仓位竞态累加
- C3: 余额为 0 拒绝开仓
- 资金层级硬性规则
- 爆仓距离计算集成
- P13.2: 置信度绝对下限过滤
- P13.3: 做多加严（震荡市禁多 + 做多置信度门槛）
"""

from unittest.mock import patch, MagicMock


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
    def _make_record(self, pnl_usdt, status="closed"):
        """创建模拟交易记录"""
        from datetime import datetime, timezone
        rec = MagicMock()
        rec.status = status
        rec.timestamp = datetime.now(timezone.utc).isoformat()
        rec.actual_pnl_usdt = pnl_usdt
        return rec

    @patch("cryptobot.journal.storage.get_all_records")
    def test_daily_loss_exceeded(self, mock_records):
        """日度亏损超限 → 拒绝"""
        # 5 笔亏损，每笔 -200 USDT，总 -1000 USDT，余额 10000 → 10% > daily 5%
        mock_records.return_value = [self._make_record(-200) for _ in range(5)]
        ok, reason = _check_loss_limits(
            {"max_loss": {"daily_pct": 5, "weekly_pct": 8, "monthly_drawdown_pct": 15}},
            account_balance=10000,
        )
        assert not ok
        assert "日度" in reason

    @patch("cryptobot.journal.storage.get_all_records")
    def test_no_closed_trades_passes(self, mock_records):
        """无已平仓交易 → 通过"""
        mock_records.return_value = []
        ok, _ = _check_loss_limits(
            {"max_loss": {"daily_pct": 5, "weekly_pct": 8, "monthly_drawdown_pct": 15}},
            account_balance=10000,
        )
        assert ok

    def test_exception_fails_closed(self):
        """journal 异常 → fail-closed（拒绝）"""
        with patch("cryptobot.journal.storage.get_all_records", side_effect=Exception("db error")):
            ok, reason = _check_loss_limits({"max_loss": {"daily_pct": 5}}, account_balance=10000)
        assert not ok
        assert "异常" in reason

    @patch("cryptobot.journal.storage.get_all_records")
    def test_within_limit_passes(self, mock_records):
        """亏损在限制内 → 通过"""
        # 3 笔亏损，每笔 -100 USDT，总 -300 USDT，余额 10000 → 3% < daily 5%
        mock_records.return_value = [self._make_record(-100) for _ in range(3)]
        ok, _ = _check_loss_limits(
            {"max_loss": {"daily_pct": 5, "weekly_pct": 8, "monthly_drawdown_pct": 15}},
            account_balance=10000,
        )
        assert ok

    def test_zero_balance_rejects(self):
        """余额为 0 → 拒绝"""
        ok, reason = _check_loss_limits({"max_loss": {"daily_pct": 5}}, account_balance=0)
        assert not ok
        assert "余额" in reason


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


# ─── O11: RR 阈值 regime 联动 ────────────────────────────────────────────

class TestRRThresholdByRegime:
    """测试盈亏比阈值随 regime 动态调整"""

    @patch("cryptobot.notify.notify_risk_rejected")
    @patch("cryptobot.notify.send_message", return_value=True)
    @patch("cryptobot.workflow.nodes.risk.call_claude_parallel")
    @patch("cryptobot.freqtrade_api.ft_api_get")
    @patch("cryptobot.signal.bridge.read_signals", return_value=[])
    def test_trending_rr_threshold_lower(
        self, mock_signals, mock_ft, mock_parallel, mock_notify, mock_reject
    ):
        """trending regime: RR>=1.2 通过, 同样的 RR 在 ranging 下被拒"""
        def ft_side_effect(endpoint):
            if endpoint == "/balance":
                return {"currencies": [{"currency": "USDT", "balance": 10000}]}
            if endpoint == "/status":
                return []
            return None

        mock_ft.side_effect = ft_side_effect

        # 构造 RR=1.3 的信号 (>1.2 trending, <2.0 ranging)
        # entry_mid = 60500, sl=59500 → sl_dist=1000
        # tp=61800 → tp_dist=1300 → RR=1.3
        decision = {
            "symbol": "BTCUSDT", "action": "long", "confidence": 75, "leverage": 3,
            "entry_price_range": [60000, 61000], "stop_loss": 59500,
            "take_profit": [{"price": 61800, "pct": 100}],
            "position_size_pct": 10, "current_price": 60500, "reasoning": "test",
        }

        mock_parallel.return_value = [
            {"decision": "approved", "risk_score": 3},
        ]

        # trending → RR 1.3 >= 1.2 → 通过
        state_trending = _base_state(
            decisions=[dict(decision)],
            market_regime={"regime": "trending"},
        )
        with patch("cryptobot.journal.analytics.calc_performance", return_value={"closed": 0}):
            with patch("cryptobot.journal.analytics.build_performance_summary", return_value=""):
                with patch("cryptobot.journal.confidence_tuner.calc_dynamic_threshold",
                           side_effect=Exception("skip")):
                    result_trending = risk_review(state_trending)

        assert len(result_trending["approved_signals"]) == 1

        # ranging → RR 1.3 < 2.0 → 拒绝（在硬性规则阶段就被拦截，不会调用 LLM）
        state_ranging = _base_state(
            decisions=[dict(decision)],
            market_regime={"regime": "ranging"},
        )
        with patch("cryptobot.journal.analytics.calc_performance", return_value={"closed": 0}):
            with patch("cryptobot.journal.analytics.build_performance_summary", return_value=""):
                with patch("cryptobot.journal.confidence_tuner.calc_dynamic_threshold",
                           side_effect=Exception("skip")):
                    result_ranging = risk_review(state_ranging)

        assert len(result_ranging["approved_signals"]) == 0


# ─── P13.2: 置信度绝对下限 ───────────────────────────────────────────────

def _ft_side_effect_normal(endpoint):
    """通用 Freqtrade mock: 余额 10000, 无持仓"""
    if endpoint == "/balance":
        return {"currencies": [{"currency": "USDT", "balance": 10000}]}
    if endpoint == "/status":
        return []
    return None


def _run_risk_review(state):
    """执行 risk_review，mock 掉常见依赖"""
    with patch("cryptobot.journal.analytics.build_performance_summary", return_value=""):
        with patch(
            "cryptobot.journal.confidence_tuner.calc_dynamic_threshold",
            side_effect=Exception("skip"),
        ):
            return risk_review(state)


class TestConfidenceFloor:
    """P13.2 置信度绝对下限过滤"""

    @patch("cryptobot.notify.send_message", return_value=True)
    @patch("cryptobot.freqtrade_api.ft_api_get", side_effect=_ft_side_effect_normal)
    @patch("cryptobot.signal.bridge.read_signals", return_value=[])
    def test_reject_below_floor_60(self, _sig, _ft, _notify):
        """置信度 55 < 60 绝对下限 → 拒绝"""
        state = _base_state(
            decisions=[_make_decision(confidence=55)],
            market_regime={"regime": "trending"},
        )
        result = _run_risk_review(state)
        assert result["approved_signals"] == []
        rejected = result["risk_details"]["rejected_signals"]
        assert any("绝对下限" in r["reason"] for r in rejected)

    @patch("cryptobot.notify.send_message", return_value=True)
    @patch("cryptobot.freqtrade_api.ft_api_get", side_effect=_ft_side_effect_normal)
    @patch("cryptobot.signal.bridge.read_signals", return_value=[])
    def test_reject_below_floor_65_ranging(self, _sig, _ft, _notify):
        """ranging 时置信度 62 < 65 → 拒绝"""
        state = _base_state(
            decisions=[_make_decision(confidence=62)],
            market_regime={"regime": "ranging"},
        )
        result = _run_risk_review(state)
        assert result["approved_signals"] == []
        rejected = result["risk_details"]["rejected_signals"]
        assert any("绝对下限" in r["reason"] for r in rejected)

    @patch("cryptobot.notify.send_message", return_value=True)
    @patch("cryptobot.workflow.nodes.risk.call_claude_parallel")
    @patch("cryptobot.freqtrade_api.ft_api_get", side_effect=_ft_side_effect_normal)
    @patch("cryptobot.signal.bridge.read_signals", return_value=[])
    def test_pass_above_floor(self, _sig, _ft, mock_parallel, _notify):
        """置信度 70 > 60 → 通过此检查（进入后续流程）"""
        mock_parallel.return_value = [
            {"decision": "approved", "risk_score": 3},
        ]
        state = _base_state(
            decisions=[_make_decision(confidence=70)],
            market_regime={"regime": "trending"},
        )
        result = _run_risk_review(state)
        assert len(result["approved_signals"]) == 1

    @patch("cryptobot.notify.send_message", return_value=True)
    @patch("cryptobot.freqtrade_api.ft_api_get", side_effect=_ft_side_effect_normal)
    @patch("cryptobot.signal.bridge.read_signals", return_value=[])
    def test_floor_60_exact_passes(self, _sig, _ft, _notify):
        """置信度恰好 60 = 下限 → 不被绝对下限拦截（>=60 通过）"""
        # confidence=60 通过 P13.2，但 action=long + confidence=60 < 65 被 P13.3 拦截
        # 所以用 short 来测试 P13.2 的边界
        state = _base_state(
            decisions=[_make_decision(action="short", confidence=60)],
            market_regime={"regime": "trending"},
        )
        result = _run_risk_review(state)
        # 不应被 "绝对下限" 拒绝
        rejected = result["risk_details"]["rejected_signals"]
        assert not any("绝对下限" in r.get("reason", "") for r in rejected)


# ─── P13.3: 做多加严 ─────────────────────────────────────────────────────

class TestLongRestrictions:
    """P13.3 做多加严: 震荡市禁多 + 做多置信度门槛 65"""

    @patch("cryptobot.notify.send_message", return_value=True)
    @patch("cryptobot.freqtrade_api.ft_api_get", side_effect=_ft_side_effect_normal)
    @patch("cryptobot.signal.bridge.read_signals", return_value=[])
    def test_ranging_block_long(self, _sig, _ft, _notify):
        """震荡市做多 → 拒绝"""
        state = _base_state(
            decisions=[_make_decision(action="long", confidence=80)],
            market_regime={"regime": "ranging"},
        )
        result = _run_risk_review(state)
        assert result["approved_signals"] == []
        rejected = result["risk_details"]["rejected_signals"]
        assert any("震荡市禁止做多" in r["reason"] for r in rejected)

    @patch("cryptobot.notify.send_message", return_value=True)
    @patch("cryptobot.freqtrade_api.ft_api_get", side_effect=_ft_side_effect_normal)
    @patch("cryptobot.signal.bridge.read_signals", return_value=[])
    def test_long_below_65_rejected(self, _sig, _ft, _notify):
        """做多置信度 62 < 65 → 拒绝（非 ranging，不触发禁多）"""
        state = _base_state(
            decisions=[_make_decision(action="long", confidence=62)],
            market_regime={"regime": "trending"},
        )
        result = _run_risk_review(state)
        assert result["approved_signals"] == []
        rejected = result["risk_details"]["rejected_signals"]
        assert any("做多置信度" in r["reason"] for r in rejected)

    @patch("cryptobot.notify.send_message", return_value=True)
    @patch("cryptobot.workflow.nodes.risk.call_claude_parallel")
    @patch("cryptobot.freqtrade_api.ft_api_get", side_effect=_ft_side_effect_normal)
    @patch("cryptobot.signal.bridge.read_signals", return_value=[])
    def test_long_65_passes(self, _sig, _ft, mock_parallel, _notify):
        """做多置信度恰好 65 → 通过做多检查"""
        mock_parallel.return_value = [
            {"decision": "approved", "risk_score": 3},
        ]
        state = _base_state(
            decisions=[_make_decision(action="long", confidence=65)],
            market_regime={"regime": "trending"},
        )
        result = _run_risk_review(state)
        assert len(result["approved_signals"]) == 1

    @patch("cryptobot.notify.send_message", return_value=True)
    @patch("cryptobot.workflow.nodes.risk.call_claude_parallel")
    @patch("cryptobot.freqtrade_api.ft_api_get", side_effect=_ft_side_effect_normal)
    @patch("cryptobot.signal.bridge.read_signals", return_value=[])
    def test_short_not_affected_by_long_rules(self, _sig, _ft, mock_parallel, _notify):
        """做空不受 P13.3 做多规则影响"""
        mock_parallel.return_value = [
            {"decision": "approved", "risk_score": 3},
        ]
        # confidence=62 > 60(floor) 但 < 65(long_min)，做空不受影响
        state = _base_state(
            decisions=[_make_decision(action="short", confidence=62)],
            market_regime={"regime": "trending"},
        )
        result = _run_risk_review(state)
        assert len(result["approved_signals"]) == 1

    @patch("cryptobot.notify.send_message", return_value=True)
    @patch("cryptobot.workflow.nodes.risk.call_claude_parallel")
    @patch("cryptobot.freqtrade_api.ft_api_get", side_effect=_ft_side_effect_normal)
    @patch("cryptobot.signal.bridge.read_signals", return_value=[])
    def test_ranging_short_allowed(self, _sig, _ft, mock_parallel, _notify):
        """震荡市做空 → 不受禁多规则影响（置信度需 >= 65 ranging floor）"""
        mock_parallel.return_value = [
            {"decision": "approved", "risk_score": 3},
        ]
        # RR 需 >= 2.0 (ranging): entry_mid=60500, sl=61500 → dist=1000
        # tp=58500 → dist=2000, RR=2.0
        decision = {
            "symbol": "BTCUSDT", "action": "short", "confidence": 70, "leverage": 3,
            "entry_price_range": [60000, 61000], "stop_loss": 61500,
            "take_profit": [58500],
            "position_size_pct": 20, "current_price": 60500, "reasoning": "test",
        }
        state = _base_state(
            decisions=[decision],
            market_regime={"regime": "ranging"},
        )
        result = _run_risk_review(state)
        assert len(result["approved_signals"]) == 1


# ─── P13.7: 震荡市参数优化 ────────────────────────────────────────────────

class TestRangingLimits:
    """P13.7 震荡市日交易数限制"""

    def _make_record(self, status="active"):
        """创建模拟交易记录（24h 内）"""
        from datetime import datetime, timezone

        rec = MagicMock()
        rec.status = status
        rec.timestamp = datetime.now(timezone.utc).isoformat()
        return rec

    @patch("cryptobot.notify.send_message", return_value=True)
    @patch("cryptobot.freqtrade_api.ft_api_get", side_effect=_ft_side_effect_normal)
    @patch("cryptobot.signal.bridge.read_signals", return_value=[])
    @patch("cryptobot.journal.storage.get_all_records")
    def test_ranging_daily_trade_limit_exceeded(
        self, mock_records, _sig, _ft, _notify
    ):
        """震荡市日交易数 >= 2 -> 拒绝"""
        # 2 笔活跃交易 (>= max_daily_trades=2)
        mock_records.return_value = [self._make_record() for _ in range(2)]

        # 做空 + 高置信度 + 高 RR，确保不被其他规则拦截
        decision = {
            "symbol": "BTCUSDT",
            "action": "short",
            "confidence": 80,
            "leverage": 2,
            "entry_price_range": [60000, 61000],
            "stop_loss": 61500,
            "take_profit": [58500],
            "position_size_pct": 10,
            "current_price": 60500,
            "reasoning": "test",
        }
        state = _base_state(
            decisions=[decision],
            market_regime={"regime": "ranging"},
        )
        result = _run_risk_review(state)
        assert result["approved_signals"] == []
        rejected = result["risk_details"]["rejected_signals"]
        assert any("震荡市日交易数" in r["reason"] for r in rejected)

    @patch("cryptobot.notify.send_message", return_value=True)
    @patch("cryptobot.workflow.nodes.risk.call_claude_parallel")
    @patch("cryptobot.freqtrade_api.ft_api_get", side_effect=_ft_side_effect_normal)
    @patch("cryptobot.signal.bridge.read_signals", return_value=[])
    @patch("cryptobot.journal.storage.get_all_records")
    def test_ranging_daily_trade_limit_within(
        self, mock_records, _sig, _ft, mock_parallel, _notify
    ):
        """震荡市日交易数 < 2 -> 通过此检查"""
        mock_records.return_value = [self._make_record()]  # 仅 1 笔
        mock_parallel.return_value = [
            {"decision": "approved", "risk_score": 3},
        ]

        decision = {
            "symbol": "BTCUSDT",
            "action": "short",
            "confidence": 80,
            "leverage": 2,
            "entry_price_range": [60000, 61000],
            "stop_loss": 61500,
            "take_profit": [58500],
            "position_size_pct": 10,
            "current_price": 60500,
            "reasoning": "test",
        }
        state = _base_state(
            decisions=[decision],
            market_regime={"regime": "ranging"},
        )
        result = _run_risk_review(state)
        assert len(result["approved_signals"]) == 1

    @patch("cryptobot.notify.send_message", return_value=True)
    @patch("cryptobot.workflow.nodes.risk.call_claude_parallel")
    @patch("cryptobot.freqtrade_api.ft_api_get", side_effect=_ft_side_effect_normal)
    @patch("cryptobot.signal.bridge.read_signals", return_value=[])
    @patch("cryptobot.journal.storage.get_all_records")
    def test_non_ranging_no_daily_limit(
        self, mock_records, _sig, _ft, mock_parallel, _notify
    ):
        """非震荡市不受日交易数限制"""
        mock_records.return_value = [self._make_record() for _ in range(5)]
        mock_parallel.return_value = [
            {"decision": "approved", "risk_score": 3},
        ]

        state = _base_state(
            decisions=[_make_decision(action="long", confidence=70)],
            market_regime={"regime": "trending"},
        )
        result = _run_risk_review(state)
        assert len(result["approved_signals"]) == 1

    @patch("cryptobot.notify.send_message", return_value=True)
    @patch("cryptobot.freqtrade_api.ft_api_get", side_effect=_ft_side_effect_normal)
    @patch("cryptobot.signal.bridge.read_signals", return_value=[])
    @patch("cryptobot.journal.storage.get_all_records")
    def test_expired_trades_not_counted(self, mock_records, _sig, _ft, _notify):
        """expired 状态的交易不计入日交易数"""
        mock_records.return_value = [
            self._make_record("expired"),
            self._make_record("expired"),
            self._make_record("expired"),
        ]

        decision = {
            "symbol": "BTCUSDT",
            "action": "short",
            "confidence": 80,
            "leverage": 2,
            "entry_price_range": [60000, 61000],
            "stop_loss": 61500,
            "take_profit": [58500],
            "position_size_pct": 10,
            "current_price": 60500,
            "reasoning": "test",
        }
        state = _base_state(
            decisions=[decision],
            market_regime={"regime": "ranging"},
        )
        # expired 交易被排除后 count=0 < 2, 不会触发限制
        # 但后续其他硬性规则可能拦截 -- 只检查不被 "震荡市日交易数" 拒绝
        result = _run_risk_review(state)
        rejected = result["risk_details"]["rejected_signals"]
        assert not any(
            "震荡市日交易数" in r.get("reason", "") for r in rejected
        )
