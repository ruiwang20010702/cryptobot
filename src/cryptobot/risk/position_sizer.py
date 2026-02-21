"""仓位计算器 (风险约束凯利公式)

核心原则:
- 单笔最大亏损 ≤ 总资金 2%
- 杠杆上限由 pairs.yaml 配置控制
- 仓位大小 = min(凯利最优, 风险约束上限)
"""

import logging

from cryptobot.config import load_settings, get_pair_config

logger = logging.getLogger(__name__)


def _load_kelly_params(symbol: str, action: str | None = None) -> tuple[float, float]:
    """从 journal 历史数据加载 Kelly 参数

    优先使用币种级别数据，fallback 到全局；样本不足时用保守默认值。

    Returns:
        (win_rate, avg_win_loss_ratio)
    """
    try:
        from cryptobot.journal.analytics import calc_performance
        perf = calc_performance(30)
        closed = perf.get("closed", 0)
        if closed < 50:
            return 0.50, 1.5

        # 币种级别胜率
        by_symbol = perf.get("by_symbol", {})
        sym_data = by_symbol.get(symbol, {})
        if sym_data.get("count", 0) >= 15:
            wr = sym_data["win_rate"]
        elif action and perf.get("by_direction", {}).get(action, {}).get("count", 0) >= 15:
            wr = perf["by_direction"][action]["win_rate"]
        else:
            wr = perf.get("win_rate", 0.35)

        # 盈亏比: gross_profit / gross_loss
        from cryptobot.journal.storage import get_all_records
        from datetime import datetime, timezone, timedelta
        cutoff = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
        records = [
            r for r in get_all_records()
            if r.status == "closed" and r.timestamp >= cutoff
        ]
        gross_profit = sum(r.actual_pnl_pct for r in records if (r.actual_pnl_pct or 0) > 0)
        gross_loss = abs(sum(r.actual_pnl_pct for r in records if (r.actual_pnl_pct or 0) < 0))
        ratio = gross_profit / gross_loss if gross_loss > 0 else 1.5

        return max(0.1, min(wr, 0.9)), max(0.5, min(ratio, 5.0))
    except Exception as e:
        logger.warning("Kelly 参数加载失败: %s, 使用保守默认值", e)
        return 0.35, 1.2


def calc_position_size(
    symbol: str,
    account_balance: float,
    entry_price: float,
    stop_loss_price: float,
    leverage: int | None = None,
    win_rate: float | None = None,
    avg_win_loss_ratio: float | None = None,
    action: str | None = None,
    confidence: int | None = None,
    regime: str = "",
) -> dict:
    """计算仓位大小

    Args:
        symbol: 交易对 (BTCUSDT)
        account_balance: 账户总资金 (USDT)
        entry_price: 入场价
        stop_loss_price: 止损价
        leverage: 杠杆倍数 (None=使用默认)
        win_rate: 历史胜率
        avg_win_loss_ratio: 平均盈亏比

    Returns:
        仓位计算结果
    """
    # 自动从 journal 加载 Kelly 参数
    if win_rate is None or avg_win_loss_ratio is None:
        auto_wr, auto_ratio = _load_kelly_params(symbol, action)
        if win_rate is None:
            win_rate = auto_wr
        if avg_win_loss_ratio is None:
            avg_win_loss_ratio = auto_ratio

    settings = load_settings()
    pair_cfg = get_pair_config(symbol)
    risk = settings.get("risk", {})

    # 杠杆
    if leverage is None:
        leverage = pair_cfg["default_leverage"] if pair_cfg else 3
    max_lev = pair_cfg["leverage_range"][1] if pair_cfg else 5
    leverage = min(leverage, max_lev)

    # 止损距离
    if entry_price <= 0 or stop_loss_price <= 0:
        raise ValueError("价格必须大于 0")

    sl_distance_pct = abs(entry_price - stop_loss_price) / entry_price * 100

    if sl_distance_pct == 0:
        raise ValueError("止损价不能等于入场价")

    # --- 方法 1: 固定风险法 ---
    max_loss_pct = risk.get("max_loss", {}).get("per_trade_pct", 2)
    max_loss_amount = account_balance * max_loss_pct / 100

    # 实际止损 = 止损距离% × 杠杆
    effective_sl_pct = sl_distance_pct * leverage / 100
    risk_position = max_loss_amount / effective_sl_pct if effective_sl_pct > 0 else 0

    # --- 方法 2: 凯利公式 ---
    # f* = (p × b - q) / b  其中 p=胜率, b=盈亏比, q=1-p
    kelly_fraction = 0
    if avg_win_loss_ratio > 0:
        q = 1 - win_rate
        kelly_fraction = (win_rate * avg_win_loss_ratio - q) / avg_win_loss_ratio
        kelly_fraction = max(0, kelly_fraction)
        # 动态半 Kelly 比例：基于 regime 和 confidence 查表
        _KELLY_SCALE = {
            ("trending", True): 0.6,
            ("trending", False): 0.5,
            ("ranging", True): 0.4,
            ("ranging", False): 0.3,
            ("volatile", True): 0.35,
            ("volatile", False): 0.25,
        }
        high_conf = confidence >= 85 if confidence is not None else False
        kelly_scale = _KELLY_SCALE.get((regime, high_conf), 0.5)
        kelly_fraction *= kelly_scale

    kelly_position = account_balance * kelly_fraction

    # --- 仓位上限约束 ---
    max_single_pct = risk.get("max_single_position_pct", 25)
    max_single_position = account_balance * max_single_pct / 100  # 保证金口径

    # 最终仓位 = min(风险法, 凯利法, 上限)，取保证金口径
    # 使用 1e-9 阈值避免浮点精度导致的近零凯利值
    margin_amount = min(risk_position, kelly_position, max_single_position) if kelly_fraction > 1e-9 else min(risk_position, max_single_position)

    # 最小金额：不足则不开仓（返回 0），由下游过滤
    min_amount = pair_cfg.get("min_amount_usdt", 50) if pair_cfg else 50
    if margin_amount < min_amount:
        margin_amount = 0

    # 名义仓位 = 保证金 × 杠杆
    notional = margin_amount * leverage

    return {
        "symbol": symbol,
        "leverage": leverage,
        "margin_usdt": round(margin_amount, 2),
        "notional_usdt": round(notional, 2),
        "sl_distance_pct": round(sl_distance_pct, 2),
        "max_loss_usdt": round(margin_amount * effective_sl_pct, 2),
        "max_loss_pct_of_balance": round(margin_amount * effective_sl_pct / account_balance * 100, 2),
        "kelly_fraction": round(kelly_fraction, 4),
        "risk_position": round(risk_position, 2),
        "kelly_position": round(kelly_position, 2),
        "account_balance": account_balance,
    }
