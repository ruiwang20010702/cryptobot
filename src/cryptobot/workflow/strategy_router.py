"""Regime 策略路由器 -- 根据市场状态选择交易策略

trending  -> ai_trend (现有 LLM 决策流)
ranging   -> mean_reversion (BB 均值回归，规则化信号)
volatile  -> observe (不交易)
混合/不确定 -> ai_trend + 降仓
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class StrategyRoute:
    """策略路由结果"""

    strategy: str  # "ai_trend" | "mean_reversion" | "observe"
    weight: float  # 1.0 = 全仓, 0.5 = 半仓, 0.0 = 不交易
    reason: str
    params: dict  # 策略特定参数


def route_strategy(
    regime: str,
    regime_confidence: float = 0.5,
    hurst: float = 0.5,
    volatility_state: str = "normal",
) -> StrategyRoute:
    """根据 regime 信息路由到合适的策略

    Args:
        regime: "trending" | "ranging" | "volatile"
        regime_confidence: regime 检测置信度 (0-1)
        hurst: Hurst 指数
        volatility_state: "normal" | "high_vol" | "low_vol"

    路由规则:
    1. volatile (ATR% > 3%) -> observe, weight=0.0
    2. trending (H>0.55, ADX>25) -> ai_trend, weight=1.0
       - 低置信度 (conf<0.5) -> ai_trend, weight=0.5
    3. ranging (H<0.45, ADX<20) -> mean_reversion, weight=0.7
    4. 混合/不确定 -> ai_trend, weight=0.5
    """
    # 1. 高波动 -> 观望
    if regime == "volatile" or volatility_state == "high_vol":
        return StrategyRoute(
            strategy="observe",
            weight=0.0,
            reason=f"高波动市场观望 (regime={regime}, vol={volatility_state})",
            params={},
        )

    # 2. 趋势市
    if regime == "trending":
        weight = 1.0 if regime_confidence >= 0.5 else 0.5
        return StrategyRoute(
            strategy="ai_trend",
            weight=weight,
            reason=f"趋势市 AI 决策 (H={hurst:.2f}, conf={regime_confidence:.2f})",
            params={"hurst": hurst},
        )

    # 3. 震荡市
    if regime == "ranging":
        return StrategyRoute(
            strategy="mean_reversion",
            weight=0.7,
            reason=f"震荡市均值回归 (H={hurst:.2f}, conf={regime_confidence:.2f})",
            params={"hurst": hurst, "max_leverage": 2},
        )

    # 4. 默认/不确定
    return StrategyRoute(
        strategy="ai_trend",
        weight=0.5,
        reason=f"市场状态不确定，降仓 AI 决策 (regime={regime})",
        params={},
    )


def route_strategies(
    regime: str,
    regime_confidence: float = 0.5,
    hurst: float = 0.5,
    volatility_state: str = "normal",
) -> list[StrategyRoute]:
    """返回多个策略路由（按权重降序排序）

    volatile → [StrategyRoute("observe", 0.0, ...)]
    trending → [StrategyRoute("ai_trend", 0.8, ...), StrategyRoute("grid", 0.2, ...)]
    ranging  → [StrategyRoute("mean_reversion", 0.5, ...), ...]

    过滤掉 weight=0 的策略。如果全部 weight=0 → 返回 [observe]。
    """
    from cryptobot.strategy.weight_tracker import get_weights

    # 高波动 → observe
    if regime == "volatile" or volatility_state == "high_vol":
        return [StrategyRoute(
            strategy="observe",
            weight=0.0,
            reason=f"高波动市场观望 (regime={regime}, vol={volatility_state})",
            params={},
        )]

    allocation = get_weights(regime)
    routes: list[StrategyRoute] = []
    for sw in allocation.weights:
        if sw.weight <= 0:
            continue
        params: dict = {}
        if sw.strategy == "mean_reversion":
            params = {"hurst": hurst, "max_leverage": 2}
        elif sw.strategy == "ai_trend":
            # 低置信度降仓
            weight = sw.weight if regime_confidence >= 0.5 else sw.weight * 0.5
            routes.append(StrategyRoute(
                strategy=sw.strategy,
                weight=weight,
                reason=f"{sw.reason} (H={hurst:.2f})",
                params={"hurst": hurst},
            ))
            continue
        routes.append(StrategyRoute(
            strategy=sw.strategy,
            weight=sw.weight,
            reason=f"{sw.reason} (H={hurst:.2f})",
            params=params,
        ))

    if not routes:
        return [StrategyRoute(
            strategy="observe",
            weight=0.0,
            reason=f"所有策略权重为 0 (regime={regime})",
            params={},
        )]

    return sorted(routes, key=lambda r: -r.weight)
