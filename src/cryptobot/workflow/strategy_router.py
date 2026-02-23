"""Regime 策略路由器 -- 根据市场状态选择交易策略

trending  -> ai_trend (现有 LLM 决策流)
ranging   -> mean_reversion (BB 均值回归，规则化信号)
volatile  -> observe (不交易) | 子状态策略 (P14 启用时)
混合/不确定 -> ai_trend + 降仓
"""

from dataclasses import dataclass

from cryptobot.config import load_settings


@dataclass(frozen=True)
class StrategyRoute:
    """策略路由结果"""

    strategy: str  # "ai_trend" | "mean_reversion" | "observe" | "funding_arb"
    weight: float  # 1.0 = 全仓, 0.5 = 半仓, 0.0 = 不交易
    reason: str
    params: dict  # 策略特定参数


def classify_volatile_subtype(
    fear_greed_value: float,
    volatility_state: str,
    settings: dict | None = None,
) -> str | None:
    """volatile regime 细分：normal/fear/greed，未启用时返回 None"""
    if settings is None:
        settings = load_settings()
    cfg = settings.get("volatile_strategy", {})
    # 自适应模式: 从 volatile_toggle 状态文件读取; 手动模式: 读 enabled 字段
    from cryptobot.evolution.volatile_toggle import is_volatile_strategy_enabled
    if not is_volatile_strategy_enabled(settings):
        return None
    if volatility_state != "high_vol":
        return None
    fear_threshold = cfg.get("fear_threshold", 20)
    greed_threshold = cfg.get("greed_threshold", 80)
    if fear_greed_value < fear_threshold:
        return "volatile_fear"
    if fear_greed_value > greed_threshold:
        return "volatile_greed"
    return "volatile_normal"


def route_strategy(
    regime: str,
    regime_confidence: float = 0.5,
    hurst: float = 0.5,
    volatility_state: str = "normal",
    fear_greed_value: float = 50,
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
    # 1. 高波动 -> 观望 or P14 子状态策略
    if regime == "volatile" or volatility_state == "high_vol":
        subtype = classify_volatile_subtype(fear_greed_value, volatility_state)
        if subtype is None:
            return StrategyRoute(
                strategy="observe",
                weight=0.0,
                reason=f"高波动市场观望 (regime={regime}, vol={volatility_state})",
                params={},
            )
        if subtype == "volatile_fear":
            return StrategyRoute(
                strategy="funding_arb",
                weight=0.6,
                reason=f"高波动恐惧: 费率套利 (FG={fear_greed_value})",
                params={"volatile_mode": True},
            )
        if subtype == "volatile_greed":
            return StrategyRoute(
                strategy="ai_trend",
                weight=0.4,
                reason=f"高波动贪婪: 做空策略 (FG={fear_greed_value})",
                params={"direction_bias": "short", "min_confidence": 80},
            )
        # volatile_normal
        return StrategyRoute(
            strategy="ai_trend",
            weight=0.3,
            reason=f"高波动中性: 保守趋势 (FG={fear_greed_value})",
            params={"max_leverage": 1, "min_confidence": 75},
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
    fear_greed_value: float = 50,
) -> list[StrategyRoute]:
    """返回多个策略路由（按权重降序排序）

    volatile → [StrategyRoute("observe", 0.0, ...)]
    trending → [StrategyRoute("ai_trend", 0.8, ...), StrategyRoute("grid", 0.2, ...)]
    ranging  → [StrategyRoute("mean_reversion", 0.5, ...), ...]

    过滤掉 weight=0 的策略。如果全部 weight=0 → 返回 [observe]。
    """
    from cryptobot.strategy.weight_tracker import get_weights

    # 高波动 → observe or P14 子状态策略
    if regime == "volatile" or volatility_state == "high_vol":
        subtype = classify_volatile_subtype(fear_greed_value, volatility_state)
        if subtype is None:
            return [StrategyRoute(
                strategy="observe",
                weight=0.0,
                reason=f"高波动市场观望 (regime={regime}, vol={volatility_state})",
                params={},
            )]
        # P14: 使用子状态权重
        allocation = get_weights(subtype)
        routes: list[StrategyRoute] = []
        for sw in allocation.weights:
            if sw.weight <= 0:
                continue
            params: dict = {}
            if sw.strategy == "funding_arb":
                params = {"volatile_mode": True}
            elif sw.strategy == "grid":
                params = {"wide_mode": True}
            elif sw.strategy == "ai_trend":
                params = {"max_leverage": 1, "hurst": hurst}
                if subtype == "volatile_greed":
                    params["direction_bias"] = "short"
                    params["min_confidence"] = 80
                elif subtype == "volatile_normal":
                    params["min_confidence"] = 75
            routes.append(StrategyRoute(
                strategy=sw.strategy,
                weight=sw.weight,
                reason=f"{sw.reason} (FG={fear_greed_value})",
                params=params,
            ))
        if not routes:
            return [StrategyRoute(
                strategy="observe",
                weight=0.0,
                reason=f"P14 子状态 {subtype} 权重全 0",
                params={},
            )]
        return sorted(routes, key=lambda r: -r.weight)

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
