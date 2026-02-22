"""Regime 策略路由器测试"""

import pytest

from cryptobot.workflow.strategy_router import route_strategy, StrategyRoute


class TestRouteStrategy:
    def test_volatile_observe(self):
        """volatile -> observe"""
        route = route_strategy("volatile")
        assert route.strategy == "observe"
        assert route.weight == 0.0

    def test_trending_ai(self):
        """trending -> ai_trend"""
        route = route_strategy("trending", regime_confidence=0.7, hurst=0.6)
        assert route.strategy == "ai_trend"
        assert route.weight == 1.0

    def test_trending_low_conf_half_weight(self):
        """trending 但低置信度 -> ai_trend, weight=0.5"""
        route = route_strategy("trending", regime_confidence=0.3)
        assert route.strategy == "ai_trend"
        assert route.weight == 0.5

    def test_ranging_mean_reversion(self):
        """ranging -> mean_reversion"""
        route = route_strategy("ranging", hurst=0.4)
        assert route.strategy == "mean_reversion"
        assert route.weight == 0.7
        assert route.params["max_leverage"] == 2

    def test_unknown_fallback(self):
        """未知 regime -> ai_trend, weight=0.5"""
        route = route_strategy("unknown")
        assert route.strategy == "ai_trend"
        assert route.weight == 0.5

    def test_high_vol_state_overrides(self):
        """volatility_state=high_vol 即使 regime=trending -> observe"""
        route = route_strategy("trending", volatility_state="high_vol")
        assert route.strategy == "observe"
        assert route.weight == 0.0

    def test_route_is_frozen(self):
        """StrategyRoute 是不可变的"""
        route = route_strategy("trending")
        with pytest.raises(AttributeError):
            route.strategy = "changed"

    def test_empty_regime_fallback(self):
        """空字符串 regime -> ai_trend, weight=0.5"""
        route = route_strategy("")
        assert route.strategy == "ai_trend"
        assert route.weight == 0.5

    def test_trending_exact_threshold(self):
        """regime_confidence 刚好 0.5 -> weight=1.0"""
        route = route_strategy("trending", regime_confidence=0.5)
        assert route.weight == 1.0

    def test_trending_just_below_threshold(self):
        """regime_confidence 刚好 0.49 -> weight=0.5"""
        route = route_strategy("trending", regime_confidence=0.49)
        assert route.weight == 0.5

    def test_reason_contains_regime(self):
        """reason 应包含 regime 信息"""
        route = route_strategy("ranging", hurst=0.4, regime_confidence=0.6)
        assert "H=0.40" in route.reason
        assert "conf=0.60" in route.reason

    def test_volatile_reason_contains_info(self):
        """volatile 的 reason 包含状态信息"""
        route = route_strategy("volatile", volatility_state="high_vol")
        assert "volatile" in route.reason
        assert "high_vol" in route.reason
