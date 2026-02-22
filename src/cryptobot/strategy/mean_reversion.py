"""布林带均值回归策略 -- 震荡市 (ranging) 专用

当价格触及布林带外轨且 RSI 极端时反转入场，
目标回归布林带中轨 (20MA)。
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class MeanReversionSignal:
    """均值回归信号"""

    symbol: str
    action: str  # "long" | "short"
    entry_price: float
    stop_loss: float  # BB 外轨 +/- 1xATR
    take_profit: list  # [{price, ratio}] 目标 BB 中轨
    confidence: int
    strategy_type: str  # "bb_mean_reversion"
    reasoning: str


def check_bb_entry(symbol: str, tech_data: dict) -> MeanReversionSignal | None:
    """检查布林带均值回归入场条件

    Args:
        symbol: 交易对
        tech_data: 从 indicators/calculator.py 获取的技术数据 dict
            需要字段: bb_upper, bb_lower, bb_mid, rsi_14, close, atr_14

    做多条件: close <= bb_lower AND rsi_14 < 35
    做空条件: close >= bb_upper AND rsi_14 > 65
    止损: BB 外轨 +/- 1xATR
    止盈: BB 中轨 (20MA)
    """
    latest = tech_data.get("latest", {})
    if not latest:
        return None

    close = latest.get("close", 0)
    bb_upper = latest.get("bb_upper", 0)
    bb_lower = latest.get("bb_lower", 0)
    bb_mid = latest.get("bb_mid", 0)
    rsi = latest.get("rsi_14", 50)
    atr = latest.get("atr_14", 0)
    volume_ratio = latest.get("volume_ratio", 1.0)

    if not all([close, bb_upper, bb_lower, bb_mid]):
        return None

    # 做多: 触及下轨 + RSI 超卖
    if close <= bb_lower and rsi < 35:
        stop_loss = bb_lower - atr  # 下轨 - 1xATR
        take_profit = [{"price": bb_mid, "ratio": 1.0}]
        conf = calc_bb_confidence(
            rsi, close, bb_lower, bb_upper, volume_ratio, "long"
        )
        return MeanReversionSignal(
            symbol=symbol,
            action="long",
            entry_price=close,
            stop_loss=round(stop_loss, 2),
            take_profit=take_profit,
            confidence=conf,
            strategy_type="bb_mean_reversion",
            reasoning=(
                f"BB下轨反转: close={close:.2f} <= bb_lower={bb_lower:.2f},"
                f" RSI={rsi:.1f}"
            ),
        )

    # 做空: 触及上轨 + RSI 超买
    if close >= bb_upper and rsi > 65:
        stop_loss = bb_upper + atr  # 上轨 + 1xATR
        take_profit = [{"price": bb_mid, "ratio": 1.0}]
        conf = calc_bb_confidence(
            rsi, close, bb_lower, bb_upper, volume_ratio, "short"
        )
        return MeanReversionSignal(
            symbol=symbol,
            action="short",
            entry_price=close,
            stop_loss=round(stop_loss, 2),
            take_profit=take_profit,
            confidence=conf,
            strategy_type="bb_mean_reversion",
            reasoning=(
                f"BB上轨反转: close={close:.2f} >= bb_upper={bb_upper:.2f},"
                f" RSI={rsi:.1f}"
            ),
        )

    return None


def calc_bb_confidence(
    rsi: float,
    close: float,
    bb_lower: float,
    bb_upper: float,
    volume_ratio: float,
    direction: str,
) -> int:
    """基于 RSI 极端度 + BB 偏离度 + 量能确认计算置信度

    基础分 50，各项加分:
    - RSI 极端度 (0-20分): RSI 越极端越高
    - BB 偏离度 (0-15分): 价格越偏离外轨越高
    - 量能确认 (0-15分): 放量反转更可靠
    """
    score = 50

    # RSI 极端度
    if direction == "long":
        rsi_extreme = max(0, 35 - rsi)  # RSI 越低越极端
    else:
        rsi_extreme = max(0, rsi - 65)  # RSI 越高越极端
    score += min(int(rsi_extreme * 0.67), 20)  # 最多 20 分

    # BB 偏离度
    bb_width = bb_upper - bb_lower
    if bb_width > 0:
        if direction == "long":
            deviation = max(0, bb_lower - close) / bb_width
        else:
            deviation = max(0, close - bb_upper) / bb_width
        score += min(int(deviation * 50), 15)  # 最多 15 分

    # 量能确认
    if volume_ratio > 1.5:
        score += 15
    elif volume_ratio > 1.2:
        score += 10
    elif volume_ratio > 1.0:
        score += 5

    return min(score, 100)


def signal_to_dict(sig: MeanReversionSignal) -> dict:
    """将 MeanReversionSignal 转为工作流兼容的 dict"""
    return {
        "symbol": sig.symbol,
        "action": sig.action,
        "entry_price_range": [sig.entry_price * 0.999, sig.entry_price * 1.001],
        "stop_loss": sig.stop_loss,
        "take_profit": sig.take_profit,
        "confidence": sig.confidence,
        "leverage": 2,  # 均值回归固定低杠杆
        "strategy_type": sig.strategy_type,
        "reasoning": sig.reasoning,
    }
