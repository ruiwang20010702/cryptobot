"""强平价格计算

公式 (逐仓模式):
  多单强平价 = 入场价 × (1 - 1/杠杆 + 维持保证金率)
  空单强平价 = 入场价 × (1 + 1/杠杆 - 维持保证金率)

爆仓距离 = |当前价 - 强平价| / 当前价 × 100%
"""

# Binance 维持保证金率 (按名义价值分级，取常用值)
# 实际应从交易所获取，这里用近似值
DEFAULT_MAINTENANCE_MARGIN_RATE = 0.004  # 0.4% (BTC/ETH 小仓位)


def calc_liquidation_price(
    entry_price: float,
    leverage: int,
    side: str,
    maintenance_margin_rate: float = DEFAULT_MAINTENANCE_MARGIN_RATE,
) -> float:
    """计算强平价格 (逐仓模式)

    Args:
        entry_price: 入场价
        leverage: 杠杆倍数
        side: "long" 或 "short"
        maintenance_margin_rate: 维持保证金率

    Returns:
        强平价格
    """
    if side == "long":
        liq = entry_price * (1 - 1 / leverage + maintenance_margin_rate)
    elif side == "short":
        liq = entry_price * (1 + 1 / leverage - maintenance_margin_rate)
    else:
        raise ValueError(f"无效方向: {side}")
    return round(liq, 2)


def calc_liquidation_distance(
    current_price: float,
    liquidation_price: float,
) -> float:
    """计算爆仓距离 (%)"""
    if current_price <= 0:
        return 0
    return round(abs(current_price - liquidation_price) / current_price * 100, 2)


def assess_liquidation_risk(distance_pct: float) -> dict:
    """评估爆仓风险等级

    返回:
        level: safe/caution/warning/danger/critical
        action: 建议操作
    """
    if distance_pct > 50:
        return {"level": "safe", "color": "green", "action": "无需操作"}
    elif distance_pct > 30:
        return {"level": "caution", "color": "yellow", "action": "记录日志，关注"}
    elif distance_pct > 20:
        return {"level": "warning", "color": "orange", "action": "Telegram 告警"}
    elif distance_pct > 10:
        return {"level": "danger", "color": "red", "action": "自动减仓 50%"}
    else:
        return {"level": "critical", "color": "red", "action": "自动全部平仓"}


def full_liquidation_analysis(
    entry_price: float,
    current_price: float,
    leverage: int,
    side: str,
    position_size_usdt: float = 0,
    maintenance_margin_rate: float = DEFAULT_MAINTENANCE_MARGIN_RATE,
) -> dict:
    """完整爆仓分析"""
    liq_price = calc_liquidation_price(entry_price, leverage, side, maintenance_margin_rate)
    distance = calc_liquidation_distance(current_price, liq_price)
    risk = assess_liquidation_risk(distance)

    # 当前盈亏
    if side == "long":
        pnl_pct = (current_price - entry_price) / entry_price * 100 * leverage
    else:
        pnl_pct = (entry_price - current_price) / entry_price * 100 * leverage

    pnl_usdt = position_size_usdt * pnl_pct / 100 if position_size_usdt else 0

    return {
        "entry_price": entry_price,
        "current_price": current_price,
        "liquidation_price": liq_price,
        "distance_pct": distance,
        "risk_level": risk["level"],
        "risk_action": risk["action"],
        "leverage": leverage,
        "side": side,
        "pnl_pct": round(pnl_pct, 2),
        "pnl_usdt": round(pnl_usdt, 2),
    }
