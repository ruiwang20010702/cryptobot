"""多策略路由测试"""

from unittest.mock import patch

from cryptobot.workflow.strategy_router import route_strategies


def test_volatile_returns_observe():
    routes = route_strategies("volatile")
    assert len(routes) == 1
    assert routes[0].strategy == "observe"
    assert routes[0].weight == 0.0


def test_high_vol_state_returns_observe():
    routes = route_strategies("trending", volatility_state="high_vol")
    assert len(routes) == 1
    assert routes[0].strategy == "observe"


def test_trending_returns_ai_trend_and_grid():
    routes = route_strategies("trending")
    assert len(routes) == 2
    assert routes[0].strategy == "ai_trend"
    assert routes[0].weight == 0.8
    assert routes[1].strategy == "grid"
    assert routes[1].weight == 0.2


def test_ranging_returns_three_strategies():
    routes = route_strategies("ranging")
    names = [r.strategy for r in routes]
    assert "mean_reversion" in names
    assert "grid" in names
    assert "ai_trend" in names


def test_low_confidence_reduces_ai_trend():
    routes = route_strategies("trending", regime_confidence=0.3)
    ai = [r for r in routes if r.strategy == "ai_trend"][0]
    assert ai.weight == 0.4  # 0.8 * 0.5


def test_sorted_by_weight_descending():
    routes = route_strategies("ranging")
    weights = [r.weight for r in routes]
    assert weights == sorted(weights, reverse=True)


def test_zero_weight_filtered():
    routes = route_strategies("trending")
    for r in routes:
        assert r.weight > 0


def test_all_zero_returns_observe():
    """volatile 默认权重全为 0 → observe"""
    # 用自定义权重模拟非 volatile regime 但权重全 0
    from cryptobot.strategy.weight_tracker import (
        StrategyWeight,
        WeightAllocation,
    )

    alloc = WeightAllocation(
        regime="custom",
        weights=[StrategyWeight("ai_trend", 0.0, "off")],
        updated_at="2026-01-01T00:00:00+00:00",
    )
    with patch(
        "cryptobot.strategy.weight_tracker.get_weights",
        return_value=alloc,
    ):
        routes = route_strategies("custom")
        assert len(routes) == 1
        assert routes[0].strategy == "observe"
