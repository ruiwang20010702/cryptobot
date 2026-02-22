"""交易执行优化器

计算最优执行窗口、资金费率结算时间、滑点估算。
帮助降低交易成本，选择更有利的入场时机。
"""

import logging
from dataclasses import dataclass
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

# Binance 永续合约结算时间 (UTC)
SETTLEMENT_HOURS = (0, 8, 16)

# 各币种基础滑点 (%)
_BASE_SLIPPAGE = {
    "BTCUSDT": 0.01,
    "ETHUSDT": 0.02,
}
_DEFAULT_SLIPPAGE = 0.03

# 滑点规模因子: position_usdt / divisor * 0.01%
_SLIPPAGE_SCALE = {
    "BTCUSDT": 10_000_000,
    "ETHUSDT": 5_000_000,
}
_DEFAULT_SCALE = 1_000_000


@dataclass(frozen=True)
class ExecutionWindow:
    """最优执行窗口建议"""

    recommended_hour_utc: int  # 0-23
    funding_rate_impact: float  # 预估资金费率影响 %
    expected_slippage: float  # 预估滑点 %
    total_cost_estimate: float  # 总成本估算 %
    reasoning: str


@dataclass(frozen=True)
class FundingSchedule:
    """资金费率结算时间表"""

    next_settlement_utc: str  # ISO timestamp
    hours_until: float
    current_rate: float  # 最近一期费率 %
    action_suggestion: str  # "enter_now" | "wait_post_settlement" | "no_preference"


@dataclass(frozen=True)
class HourlyCostProfile:
    """每小时成本概况"""

    hour_utc: int  # 0-23
    avg_slippage: float  # 该时段平均滑点 %
    funding_rate_applies: bool  # 该时段是否有资金费率结算
    total_cost: float  # 总成本 %


def _get_funding_rate(symbol: str) -> float:
    """尝试获取当前资金费率，失败返回 0"""
    try:
        from cryptobot.data.onchain import get_funding_rate

        data = get_funding_rate(symbol, limit=1)
        return data.get("current_rate", 0.0)
    except Exception:
        logger.debug("获取资金费率失败 %s, 使用默认值 0", symbol)
        return 0.0


def _get_spread_pct(symbol: str) -> float:
    """尝试从订单簿缓存获取 spread，失败返回默认值"""
    try:
        from cryptobot.data.orderbook import get_orderbook_depth

        data = get_orderbook_depth(symbol, limit=5)
        spread = data.get("spread_pct", 0.0)
        if spread > 0:
            return spread
    except Exception:
        logger.debug("获取订单簿失败 %s, 使用默认滑点", symbol)
    return _BASE_SLIPPAGE.get(symbol, _DEFAULT_SLIPPAGE)


def _next_settlement(now: datetime) -> datetime:
    """计算下一个结算时间点

    Binance 永续合约每 8 小时结算: 00:00, 08:00, 16:00 UTC
    """
    current_hour = now.hour
    current_minutes = now.minute + now.second / 60

    for h in SETTLEMENT_HOURS:
        if h > current_hour or (h == current_hour and current_minutes == 0):
            return now.replace(hour=h, minute=0, second=0, microsecond=0)

    # 当天所有结算时间已过，取次日 00:00
    next_day = now.replace(hour=0, minute=0, second=0, microsecond=0)
    from datetime import timedelta

    return next_day + timedelta(days=1)


def _hours_between(start: datetime, end: datetime) -> float:
    """计算两个时间点之间的小时数"""
    delta = end - start
    return delta.total_seconds() / 3600


def calc_funding_schedule(
    symbol: str,
    now: datetime | None = None,
) -> FundingSchedule:
    """计算下次资金费率结算时间

    Binance 永续合约结算时间: 00:00, 08:00, 16:00 UTC
    """
    if now is None:
        now = datetime.now(timezone.utc)

    next_settle = _next_settlement(now)
    hours_until = _hours_between(now, next_settle)
    current_rate = _get_funding_rate(symbol)

    # 决策建议
    if abs(current_rate) < 0.005:
        suggestion = "no_preference"
    elif hours_until <= 1.0:
        # 距结算不到 1 小时，建议等结算后
        suggestion = "wait_post_settlement"
    else:
        suggestion = "enter_now"

    return FundingSchedule(
        next_settlement_utc=next_settle.isoformat(),
        hours_until=round(hours_until, 2),
        current_rate=current_rate,
        action_suggestion=suggestion,
    )


def estimate_slippage(
    symbol: str,
    position_usdt: float,
    action: str,
) -> float:
    """估算滑点

    基于订单簿深度或默认值:
    - BTC: 0.01% 基础 + position_usdt / 10_000_000 * 0.01%
    - ETH: 0.02% 基础 + position_usdt / 5_000_000 * 0.01%
    - 其他: 0.03% 基础 + position_usdt / 1_000_000 * 0.01%
    """
    base = _BASE_SLIPPAGE.get(symbol, _DEFAULT_SLIPPAGE)
    scale_divisor = _SLIPPAGE_SCALE.get(symbol, _DEFAULT_SCALE)
    size_impact = position_usdt / scale_divisor * 0.01
    return round(base + size_impact, 6)


def calc_optimal_execution_window(
    symbol: str,
    action: str,
    urgency: str = "normal",
    now: datetime | None = None,
) -> ExecutionWindow:
    """计算最优执行窗口

    逻辑:
    1. 获取当前资金费率
    2. Binance 永续每 8 小时结算一次 (00:00, 08:00, 16:00 UTC)
    3. 如果当前费率 > 0 且要 long: 建议在结算后立即入场（刚付完费）
    4. 如果当前费率 < 0 且要 short: 建议在结算后立即入场
    5. urgent 模式: 忽略费率优化，立即执行
    6. patient 模式: 等待最优窗口
    """
    if now is None:
        now = datetime.now(timezone.utc)

    current_hour = now.hour
    funding_rate = _get_funding_rate(symbol)
    slippage = _get_spread_pct(symbol)

    # urgent 模式: 立即执行
    if urgency == "urgent":
        total = slippage + abs(funding_rate) * 100
        return ExecutionWindow(
            recommended_hour_utc=current_hour,
            funding_rate_impact=round(funding_rate * 100, 4),
            expected_slippage=round(slippage, 4),
            total_cost_estimate=round(total, 4),
            reasoning="urgent 模式: 忽略费率优化，立即执行",
        )

    # 计算推荐时间
    next_settle = _next_settlement(now)
    hours_until = _hours_between(now, next_settle)

    # 判断是否应等待结算后
    should_wait = False
    reasoning_parts = []

    if funding_rate > 0 and action == "long":
        # 正费率做多 → 多头付费，结算后入场更优
        should_wait = True
        reasoning_parts.append(
            f"正费率({funding_rate:.4%})做多, 建议结算后入场避免支付费用"
        )
    elif funding_rate < 0 and action == "short":
        # 负费率做空 → 空头付费，结算后入场更优
        should_wait = True
        reasoning_parts.append(
            f"负费率({funding_rate:.4%})做空, 建议结算后入场避免支付费用"
        )
    elif funding_rate > 0 and action == "short":
        reasoning_parts.append(
            f"正费率({funding_rate:.4%})做空, 可收取费用, 随时入场"
        )
    elif funding_rate < 0 and action == "long":
        reasoning_parts.append(
            f"负费率({funding_rate:.4%})做多, 可收取费用, 随时入场"
        )
    else:
        reasoning_parts.append("费率中性, 无特殊偏好")

    # patient 模式: 倾向等待
    if urgency == "patient" and hours_until <= 4:
        should_wait = True
        reasoning_parts.append(
            f"patient 模式, 距结算仅 {hours_until:.1f}h, 建议等待"
        )

    if should_wait:
        recommended_hour = next_settle.hour
        funding_impact = 0.0  # 结算后入场无费率影响
    else:
        recommended_hour = current_hour
        funding_impact = abs(funding_rate) * 100

    total = slippage + funding_impact
    reasoning = "; ".join(reasoning_parts)

    return ExecutionWindow(
        recommended_hour_utc=recommended_hour,
        funding_rate_impact=round(funding_impact, 4),
        expected_slippage=round(slippage, 4),
        total_cost_estimate=round(total, 4),
        reasoning=reasoning,
    )
