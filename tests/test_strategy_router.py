"""Regime 策略路由器测试"""

from unittest.mock import patch

import pytest

from cryptobot.workflow.strategy_router import (
    classify_volatile_subtype,
    route_strategy,
    StrategyRoute,
)


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


# ─── P14: classify_volatile_subtype 测试 ──────────────────


class TestClassifyVolatileSubtype:
    def test_disabled_returns_none(self):
        """未启用时返回 None"""
        settings = {"volatile_strategy": {"enabled": False}}
        result = classify_volatile_subtype(15, "high_vol", settings)
        assert result is None

    def test_not_high_vol_returns_none(self):
        """非 high_vol 状态返回 None"""
        settings = {"volatile_strategy": {"enabled": True}}
        result = classify_volatile_subtype(15, "normal", settings)
        assert result is None

    def test_fear_below_threshold(self):
        """FG < 20 → volatile_fear"""
        settings = {"volatile_strategy": {"enabled": True, "fear_threshold": 20, "greed_threshold": 80}}
        result = classify_volatile_subtype(15, "high_vol", settings)
        assert result == "volatile_fear"

    def test_greed_above_threshold(self):
        """FG > 80 → volatile_greed"""
        settings = {"volatile_strategy": {"enabled": True, "fear_threshold": 20, "greed_threshold": 80}}
        result = classify_volatile_subtype(85, "high_vol", settings)
        assert result == "volatile_greed"

    def test_normal_middle_range(self):
        """20 <= FG <= 80 → volatile_normal"""
        settings = {"volatile_strategy": {"enabled": True, "fear_threshold": 20, "greed_threshold": 80}}
        result = classify_volatile_subtype(50, "high_vol", settings)
        assert result == "volatile_normal"

    def test_fear_exact_threshold(self):
        """FG == 20 → volatile_normal (not fear)"""
        settings = {"volatile_strategy": {"enabled": True, "fear_threshold": 20, "greed_threshold": 80}}
        result = classify_volatile_subtype(20, "high_vol", settings)
        assert result == "volatile_normal"

    def test_greed_exact_threshold(self):
        """FG == 80 → volatile_normal (not greed)"""
        settings = {"volatile_strategy": {"enabled": True, "fear_threshold": 20, "greed_threshold": 80}}
        result = classify_volatile_subtype(80, "high_vol", settings)
        assert result == "volatile_normal"

    def test_custom_thresholds(self):
        """自定义阈值"""
        settings = {"volatile_strategy": {"enabled": True, "fear_threshold": 30, "greed_threshold": 70}}
        assert classify_volatile_subtype(25, "high_vol", settings) == "volatile_fear"
        assert classify_volatile_subtype(75, "high_vol", settings) == "volatile_greed"
        assert classify_volatile_subtype(50, "high_vol", settings) == "volatile_normal"

    def test_no_config_returns_none(self):
        """无 volatile_strategy 配置 → None"""
        result = classify_volatile_subtype(15, "high_vol", settings={})
        assert result is None

    @patch("cryptobot.evolution.volatile_toggle._load_state")
    def test_auto_mode_enabled_via_state(self, mock_state):
        """auto=true, 状态文件 enabled=true → 识别子状态"""
        from cryptobot.evolution.volatile_toggle import VolatileToggleState
        mock_state.return_value = VolatileToggleState(enabled=True)
        settings = {"volatile_strategy": {"auto": True, "enabled": False, "fear_threshold": 20, "greed_threshold": 80}}
        result = classify_volatile_subtype(50, "high_vol", settings)
        assert result == "volatile_normal"

    @patch("cryptobot.evolution.volatile_toggle._load_state")
    def test_auto_mode_disabled_via_state(self, mock_state):
        """auto=true, 状态文件 enabled=false → None"""
        from cryptobot.evolution.volatile_toggle import VolatileToggleState
        mock_state.return_value = VolatileToggleState(enabled=False)
        settings = {"volatile_strategy": {"auto": True, "enabled": True}}
        result = classify_volatile_subtype(50, "high_vol", settings)
        assert result is None


# ─── P14: route_strategy volatile 子状态测试 ──────────────


class TestRouteStrategyP14:
    _ENABLED_SETTINGS = {"volatile_strategy": {"enabled": True, "fear_threshold": 20, "greed_threshold": 80}}

    @patch("cryptobot.workflow.strategy_router.load_settings")
    def test_volatile_fear_routes_to_funding_arb(self, mock_settings):
        mock_settings.return_value = self._ENABLED_SETTINGS
        route = route_strategy("volatile", volatility_state="high_vol", fear_greed_value=10)
        assert route.strategy == "funding_arb"
        assert route.weight == 0.6
        assert route.params.get("volatile_mode") is True

    @patch("cryptobot.workflow.strategy_router.load_settings")
    def test_volatile_greed_routes_to_ai_trend_short(self, mock_settings):
        mock_settings.return_value = self._ENABLED_SETTINGS
        route = route_strategy("volatile", volatility_state="high_vol", fear_greed_value=90)
        assert route.strategy == "ai_trend"
        assert route.weight == 0.4
        assert route.params.get("direction_bias") == "short"

    @patch("cryptobot.workflow.strategy_router.load_settings")
    def test_volatile_normal_routes_to_conservative(self, mock_settings):
        mock_settings.return_value = self._ENABLED_SETTINGS
        route = route_strategy("volatile", volatility_state="high_vol", fear_greed_value=50)
        assert route.strategy == "ai_trend"
        assert route.weight == 0.3
        assert route.params.get("max_leverage") == 1

    @patch("cryptobot.workflow.strategy_router.load_settings")
    def test_disabled_still_observe(self, mock_settings):
        mock_settings.return_value = {"volatile_strategy": {"enabled": False}}
        route = route_strategy("volatile", volatility_state="high_vol", fear_greed_value=10)
        assert route.strategy == "observe"
        assert route.weight == 0.0
