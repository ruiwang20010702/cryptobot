"""P17: 做多策略诊断与不对称约束测试

覆盖:
- B1: 做多杠杆上限 2x
- B2: direction_bias 硬性拦截
- B4: 做多 30d 滚动胜率门控
- B3: Kelly 三维查表（做多缩放降低）
- A1: 方向分拆置信度校准
- A4: strategy_router trend_direction 传递
"""

from datetime import datetime, timezone, timedelta
from unittest.mock import MagicMock, patch

import pytest

from cryptobot.workflow.nodes.risk import _apply_hard_rules


# ─── 通用辅助 ──────────────────────────────────────────────────────────


def _make_decision(
    symbol="BTCUSDT", action="long", confidence=75, leverage=3,
):
    return {
        "symbol": symbol,
        "action": action,
        "confidence": confidence,
        "leverage": leverage,
        "entry_price_range": [60000, 61000],
        "stop_loss": 59000,
        "take_profit": [{"price": 63000, "pct": 100}],
        "position_size_pct": 10,
        "current_price": 60500,
        "reasoning": "test",
    }


def _noop_cb():
    """无熔断状态"""
    cb = MagicMock()
    cb.action = "none"
    cb.block_long = False
    return cb


_DEFAULT_RISK_CFG = {
    "confidence_floor": 60,
    "confidence_floor_ranging": 65,
    "long_min_confidence": 65,
    "ranging_block_long": True,
    "long_max_leverage": 2,
    "max_total_position_pct": 80,
    "max_same_direction_pct": 50,
}

_DEFAULT_SETTINGS = {"market_regime": {"ranging": {"max_daily_trades": 2}}}


def _call_hard_rules(decision, regime=None, risk_cfg=None, preloaded_records=None,
                     route_params=None, **kwargs):
    """简化调用 _apply_hard_rules"""
    return _apply_hard_rules(
        decision=decision,
        regime=regime or {"regime": "trending"},
        risk_cfg=risk_cfg or _DEFAULT_RISK_CFG,
        settings=kwargs.get("settings", _DEFAULT_SETTINGS),
        cb_state=kwargs.get("cb_state", _noop_cb()),
        merged_params=kwargs.get("merged_params", {}),
        account_balance=kwargs.get("account_balance", 10000),
        positions=kwargs.get("positions", []),
        approved=kwargs.get("approved", []),
        preloaded_records=preloaded_records,
        total_used=kwargs.get("total_used", 0),
        long_used=kwargs.get("long_used", 0),
        short_used=kwargs.get("short_used", 0),
        route_params=route_params,
    )


# ─── B1: 做多杠杆上限 ─────────────────────────────────────────────────


class TestLongMaxLeverage:
    def test_long_leverage_clamped_to_2(self):
        """做多杠杆 5x → 钳位到 2x"""
        hr = _call_hard_rules(_make_decision(leverage=5))
        assert hr["passed"]
        assert hr["decision"]["leverage"] == 2
        checks = hr["hard_result"]["checks"]
        assert any(c["rule"] == "long_max_leverage" for c in checks)

    def test_long_leverage_at_limit_unchanged(self):
        """做多杠杆 2x → 不变"""
        hr = _call_hard_rules(_make_decision(leverage=2))
        assert hr["passed"]
        assert hr["decision"]["leverage"] == 2

    def test_long_leverage_below_limit_unchanged(self):
        """做多杠杆 1x → 不变"""
        hr = _call_hard_rules(_make_decision(leverage=1))
        assert hr["passed"]
        assert hr["decision"]["leverage"] == 1

    def test_short_leverage_not_clamped(self):
        """做空杠杆不受做多上限影响"""
        hr = _call_hard_rules(_make_decision(action="short", leverage=3, confidence=62))
        assert hr["passed"]
        # 做空不走 P17 分支，杠杆不被 long_max_leverage 钳位
        assert hr["decision"]["leverage"] == 3

    def test_custom_long_max_leverage(self):
        """自定义做多杠杆上限"""
        cfg = {**_DEFAULT_RISK_CFG, "long_max_leverage": 3}
        hr = _call_hard_rules(_make_decision(leverage=5), risk_cfg=cfg)
        assert hr["decision"]["leverage"] == 3


# ─── B2: direction_bias 硬性拦截 ──────────────────────────────────────


class TestDirectionBiasBlock:
    def test_short_bias_blocks_long(self):
        """direction_bias=short + action=long → 拒绝"""
        hr = _call_hard_rules(
            _make_decision(action="long", confidence=80),
            route_params={"direction_bias": "short"},
        )
        assert not hr["passed"]
        assert "仅做空" in hr["rejected"]["reason"]

    def test_short_bias_allows_short(self):
        """direction_bias=short + action=short → 通过"""
        hr = _call_hard_rules(
            _make_decision(action="short", confidence=62),
            route_params={"direction_bias": "short"},
        )
        assert hr["passed"]

    def test_no_bias_allows_long(self):
        """无 direction_bias → 做多通过"""
        hr = _call_hard_rules(
            _make_decision(action="long", confidence=75),
            route_params={},
        )
        assert hr["passed"]

    def test_none_route_params_allows_long(self):
        """route_params=None → 做多通过"""
        hr = _call_hard_rules(
            _make_decision(action="long", confidence=75),
            route_params=None,
        )
        assert hr["passed"]


# ─── B4: 做多 30d 滚动胜率门控 ───────────────────────────────────────


def _make_record(action="long", status="closed", pnl_pct=1.0, days_ago=5):
    """创建模拟交易记录"""
    rec = MagicMock()
    rec.action = action
    rec.status = status
    rec.actual_pnl_pct = pnl_pct
    rec.timestamp = (
        datetime.now(timezone.utc) - timedelta(days=days_ago)
    ).isoformat()
    return rec


class TestLongWinRateGating:
    def test_long_wr_below_30_rejects(self):
        """做多 30d 胜率 < 30% → 拒绝"""
        # 12 笔做多，2 赢 10 亏 → 16.7% < 30%
        records = (
            [_make_record(pnl_pct=2.0)] * 2
            + [_make_record(pnl_pct=-1.0)] * 10
        )
        hr = _call_hard_rules(
            _make_decision(action="long", confidence=75),
            preloaded_records=records,
        )
        assert not hr["passed"]
        assert "30d 胜率" in hr["rejected"]["reason"]

    def test_long_wr_30_to_40_degrades_leverage(self):
        """做多 30d 胜率 30%-40% → 杠杆降级"""
        # 10 笔做多，3 赢 7 亏 → 30% (not < 30%, not >= 40%)
        records = (
            [_make_record(pnl_pct=2.0)] * 3
            + [_make_record(pnl_pct=-1.0)] * 7
        )
        hr = _call_hard_rules(
            _make_decision(action="long", confidence=75, leverage=2),
            preloaded_records=records,
        )
        assert hr["passed"]
        assert hr["decision"]["leverage"] == 1  # 2 - 1 = 1

    def test_long_wr_above_40_no_degradation(self):
        """做多 30d 胜率 >= 40% → 不降级"""
        # 10 笔做多，5 赢 5 亏 → 50%
        records = (
            [_make_record(pnl_pct=2.0)] * 5
            + [_make_record(pnl_pct=-1.0)] * 5
        )
        hr = _call_hard_rules(
            _make_decision(action="long", confidence=75, leverage=2),
            preloaded_records=records,
        )
        assert hr["passed"]
        assert hr["decision"]["leverage"] == 2

    def test_insufficient_samples_no_gating(self):
        """做多记录 < 10 笔 → 不触发门控"""
        records = [_make_record(pnl_pct=-1.0)] * 5  # 5 < 10
        hr = _call_hard_rules(
            _make_decision(action="long", confidence=75),
            preloaded_records=records,
        )
        assert hr["passed"]

    def test_short_records_excluded(self):
        """做空记录不计入做多胜率"""
        # 5 笔做多（3赢）+ 10 笔做空（0赢）→ 做多只有5笔 < 10 → 不触发
        records = (
            [_make_record(action="long", pnl_pct=2.0)] * 3
            + [_make_record(action="long", pnl_pct=-1.0)] * 2
            + [_make_record(action="short", pnl_pct=-1.0)] * 10
        )
        hr = _call_hard_rules(
            _make_decision(action="long", confidence=75),
            preloaded_records=records,
        )
        assert hr["passed"]  # 只有 5 笔做多 < 10

    def test_old_records_excluded(self):
        """超过 30d 的做多记录不计入"""
        # 10 笔做多，全亏损但都是 40 天前的
        records = [_make_record(pnl_pct=-1.0, days_ago=40)] * 10
        hr = _call_hard_rules(
            _make_decision(action="long", confidence=75),
            preloaded_records=records,
        )
        assert hr["passed"]  # 30d 内无记录

    def test_short_not_gated(self):
        """做空不受做多胜率门控影响"""
        records = [_make_record(action="long", pnl_pct=-1.0)] * 12
        hr = _call_hard_rules(
            _make_decision(action="short", confidence=62),
            preloaded_records=records,
        )
        assert hr["passed"]


# ─── B3: Kelly 三维查表 ──────────────────────────────────────────────


class TestKellyThreeDimensional:
    """P17-B3: Kelly 缩放因子三维查表 (regime, high_conf, is_long)"""

    def _calc_kelly_fraction(
        self, regime: str, confidence: int, action: str = "short",
    ) -> float:
        from cryptobot.risk.position_sizer import calc_position_size
        result = calc_position_size(
            symbol="BTCUSDT",
            account_balance=10000,
            entry_price=60000,
            stop_loss_price=58000 if action == "long" else 62000,
            leverage=3,
            win_rate=0.6,
            avg_win_loss_ratio=2.0,
            confidence=confidence,
            regime=regime,
            action=action,
        )
        return result["kelly_fraction"]

    def test_trending_short_higher_than_long(self):
        """trending: 做空 Kelly > 做多 Kelly"""
        short_kf = self._calc_kelly_fraction("trending", 90, "short")
        long_kf = self._calc_kelly_fraction("trending", 90, "long")
        assert short_kf > long_kf

    def test_trending_high_conf_short(self):
        """trending + high_conf + short → scale=0.6"""
        kf = self._calc_kelly_fraction("trending", 90, "short")
        # raw kelly = 0.4, scale=0.6 → 0.24
        assert kf == pytest.approx(0.24, abs=0.001)

    def test_trending_high_conf_long(self):
        """trending + high_conf + long → scale=0.4"""
        kf = self._calc_kelly_fraction("trending", 90, "long")
        # raw kelly = 0.4, scale=0.4 → 0.16
        assert kf == pytest.approx(0.16, abs=0.001)

    def test_volatile_low_conf_long_smallest(self):
        """volatile + low_conf + long → 最小缩放 0.15"""
        kf = self._calc_kelly_fraction("volatile", 50, "long")
        # raw kelly = 0.4, scale=0.15 → 0.06
        assert kf == pytest.approx(0.06, abs=0.001)

    def test_unknown_regime_long_default(self):
        """未知 regime + long → 默认 0.25"""
        kf = self._calc_kelly_fraction("unknown", 70, "long")
        # raw kelly = 0.4, scale=0.25 → 0.10
        assert kf == pytest.approx(0.10, abs=0.001)

    def test_unknown_regime_short_default(self):
        """未知 regime + short → 默认 0.5"""
        kf = self._calc_kelly_fraction("unknown", 70, "short")
        # raw kelly = 0.4, scale=0.5 → 0.20
        assert kf == pytest.approx(0.20, abs=0.001)


# ─── A1: 方向分拆置信度校准 ──────────────────────────────────────────


class TestDirectionCalibration:
    """P17-A1: 方向分拆置信度校准"""

    @patch("cryptobot.journal.analytics.get_all_records")
    def test_calibration_long_separate(self, mock_records):
        """做多方向独立校准"""
        from cryptobot.journal.analytics import calc_performance

        records = []
        for i in range(60):
            r = MagicMock()
            r.status = "closed"
            r.action = "long" if i % 2 == 0 else "short"
            r.confidence = 65
            r.actual_pnl_pct = 5.0 if i < 20 else -2.0
            r.actual_pnl_usdt = 50.0 if i < 20 else -20.0
            r.timestamp = datetime.now(timezone.utc).isoformat()
            r.analyst_votes = None
            r.symbol = "BTCUSDT"
            records.append(r)

        mock_records.return_value = records
        perf = calc_performance(30)
        assert "confidence_calibration_long" in perf
        assert "confidence_calibration_short" in perf

    @patch("cryptobot.journal.analytics.calc_performance")
    def test_long_threshold_in_dynamic(self, mock_perf):
        """confidence_tuner 返回做多专属阈值"""
        from cryptobot.journal.confidence_tuner import calc_dynamic_threshold

        mock_perf.return_value = {
            "closed": 55,
            "confidence_calibration": {
                "60-70": {"count": 18, "actual_win_rate": 0.60},
                "70-80": {"count": 18, "actual_win_rate": 0.72},
                "80-90": {"count": 15, "actual_win_rate": 0.82},
            },
            "confidence_calibration_long": {
                "60-70": {"count": 18, "actual_win_rate": 0.30},  # 严重偏乐观
                "70-80": {"count": 15, "actual_win_rate": 0.50},
                "80-90": {"count": 5, "actual_win_rate": None},
            },
            "confidence_calibration_short": {
                "60-70": {"count": 15, "actual_win_rate": 0.70},
                "70-80": {"count": 15, "actual_win_rate": 0.80},
                "80-90": {"count": 5, "actual_win_rate": None},
            },
        }
        result = calc_dynamic_threshold()
        assert "recommended_long_min_confidence" in result
        # 做多校准偏乐观 → 做多阈值应 > 65
        assert result["recommended_long_min_confidence"] > 65


# ─── A4: strategy_router trend_direction ──────────────────────────────


class TestTrendDirectionRouting:
    """P17-A4: trend_direction 传递到策略路由"""

    def test_trending_down_adds_short_bias(self):
        """trending + down → direction_bias=short"""
        from cryptobot.workflow.strategy_router import route_strategy

        route = route_strategy(
            regime="trending",
            hurst=0.6,
            trend_direction="down",
        )
        assert route.params.get("direction_bias") == "short"
        assert route.params.get("min_confidence") == 75

    def test_trending_up_no_bias(self):
        """trending + up → 无 direction_bias"""
        from cryptobot.workflow.strategy_router import route_strategy

        route = route_strategy(
            regime="trending",
            hurst=0.6,
            trend_direction="up",
        )
        assert "direction_bias" not in route.params

    def test_trending_no_direction_no_bias(self):
        """trending + 无方向 → 无 direction_bias"""
        from cryptobot.workflow.strategy_router import route_strategy

        route = route_strategy(
            regime="trending",
            hurst=0.6,
            trend_direction="",
        )
        assert "direction_bias" not in route.params

    def test_route_strategies_trending_down(self):
        """route_strategies: trending + down → ai_trend 有 short bias"""
        from cryptobot.workflow.strategy_router import route_strategies

        routes = route_strategies(
            regime="trending",
            hurst=0.6,
            trend_direction="down",
        )
        ai_routes = [r for r in routes if r.strategy == "ai_trend"]
        assert ai_routes
        assert ai_routes[0].params.get("direction_bias") == "short"

    def test_ranging_ignores_trend_direction(self):
        """ranging 不受 trend_direction 影响"""
        from cryptobot.workflow.strategy_router import route_strategy

        route = route_strategy(
            regime="ranging",
            hurst=0.4,
            trend_direction="down",
        )
        assert "direction_bias" not in route.params
