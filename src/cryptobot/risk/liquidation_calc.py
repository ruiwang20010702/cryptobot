"""强平价格计算

公式 (逐仓模式):
  多单强平价 = 入场价 × (1 - 1/杠杆 + 维持保证金率)
  空单强平价 = 入场价 × (1 + 1/杠杆 - 维持保证金率)

爆仓距离 = |当前价 - 强平价| / 当前价 × 100%
"""

# Binance 维持保证金率 (按名义价值分级，取常用值)
# 实际应从交易所获取，这里用近似值
DEFAULT_MAINTENANCE_MARGIN_RATE = 0.004  # 0.4% (BTC/ETH 小仓位)

# Binance USDT-M 维持保证金分级表 (简化版, 2026-02)
# 格式: [(名义价值上限 USD, 维持保证金率), ...]
_TIERED_MMR: dict[str, list[tuple[float, float]]] = {
    "BTCUSDT": [
        (50_000, 0.004),
        (250_000, 0.005),
        (1_000_000, 0.01),
        (5_000_000, 0.025),
        (float("inf"), 0.05),
    ],
    "ETHUSDT": [
        (50_000, 0.004),
        (250_000, 0.005),
        (1_000_000, 0.01),
        (5_000_000, 0.025),
        (float("inf"), 0.05),
    ],
}
# Altcoin 通用分级 (保证金率更高)
_ALTCOIN_TIERED_MMR = [
    (10_000, 0.01),
    (50_000, 0.015),
    (250_000, 0.02),
    (1_000_000, 0.025),
    (float("inf"), 0.05),
]


def _get_maintenance_margin_rate(symbol: str, notional: float) -> float:
    """按 Binance 分级表查询维持保证金率

    Args:
        symbol: 交易对 (BTCUSDT)
        notional: 名义价值 (USD)

    Returns:
        维持保证金率 (如 0.004 表示 0.4%)
    """
    tiers = _TIERED_MMR.get(symbol, _ALTCOIN_TIERED_MMR)
    for limit, rate in tiers:
        if notional <= limit:
            return rate
    return DEFAULT_MAINTENANCE_MARGIN_RATE


def calc_liquidation_price(
    entry_price: float,
    leverage: int,
    side: str,
    maintenance_margin_rate: float | None = None,
    safety_buffer_pct: float = 2,
    symbol: str = "",
    position_size_usdt: float = 0,
) -> float:
    """计算强平价格 (逐仓模式，含安全缓冲)

    Args:
        entry_price: 入场价
        leverage: 杠杆倍数
        side: "long" 或 "short"
        maintenance_margin_rate: 维持保证金率 (None=按分级表自动查询)
        safety_buffer_pct: 安全缓冲百分比，使预估更保守 (默认 2%)
        symbol: 交易对 (用于分级查询)
        position_size_usdt: 保证金 (用于计算名义价值查分级)

    Returns:
        强平价格 (偏保守)
    """
    if maintenance_margin_rate is None:
        if symbol:
            notional = (
                position_size_usdt * leverage if position_size_usdt
                else entry_price * leverage
            )
            maintenance_margin_rate = _get_maintenance_margin_rate(symbol, notional)
        else:
            maintenance_margin_rate = DEFAULT_MAINTENANCE_MARGIN_RATE
    if side == "long":
        liq = entry_price * (1 - 1 / leverage + maintenance_margin_rate)
        # 多单: 强平价上移 (更保守)
        liq *= (1 + safety_buffer_pct / 100)
    elif side == "short":
        liq = entry_price * (1 + 1 / leverage - maintenance_margin_rate)
        # 空单: 强平价下移 (更保守)
        liq *= (1 - safety_buffer_pct / 100)
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


def assess_liquidation_risk(distance_pct: float, leverage: int = 3) -> dict:
    """评估爆仓风险等级

    高杠杆时阈值更严格: factor = min(5 / leverage, 2.0)

    返回:
        level: safe/caution/warning/danger/critical
        action: 建议操作
    """
    # 高杠杆缩放阈值 (5x 基准, 10x 杠杆阈值翻倍)
    factor = min(5 / max(leverage, 1), 2.0)
    if distance_pct > 50 * factor:
        return {"level": "safe", "color": "green", "action": "无需操作"}
    elif distance_pct > 30 * factor:
        return {"level": "caution", "color": "yellow", "action": "记录日志，关注"}
    elif distance_pct > 20 * factor:
        return {"level": "warning", "color": "orange", "action": "Telegram 告警"}
    elif distance_pct > 10 * factor:
        return {"level": "danger", "color": "red", "action": "自动减仓 50%"}
    else:
        return {"level": "critical", "color": "red", "action": "自动全部平仓"}


def full_liquidation_analysis(
    entry_price: float,
    current_price: float,
    leverage: int,
    side: str,
    position_size_usdt: float = 0,
    maintenance_margin_rate: float | None = None,
    symbol: str = "",
) -> dict:
    """完整爆仓分析"""
    if maintenance_margin_rate is None:
        notional = position_size_usdt * leverage if position_size_usdt else entry_price * leverage
        maintenance_margin_rate = _get_maintenance_margin_rate(symbol, notional)
    liq_price = calc_liquidation_price(entry_price, leverage, side, maintenance_margin_rate)
    distance = calc_liquidation_distance(current_price, liq_price)
    risk = assess_liquidation_risk(distance, leverage=leverage)

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
