"""交易模拟器 — 信号 + 1h K线 → 逐根扫描 → TradeResult

核心逻辑:
1. 入场价 = mid(entry_range) ± 方向滑点
2. 逐根 1h K线: 止损优先于止盈（同根先检查不利方向）
3. 分批止盈: take_profit 列表逐级触发
4. 7 天超时以收盘价平仓
"""

from dataclasses import dataclass

import pandas as pd

from cryptobot.backtest.cost_model import CostConfig, calc_trade_costs


@dataclass(frozen=True)
class TradeResult:
    """单笔交易模拟结果"""

    symbol: str
    action: str  # long / short
    entry_price: float
    exit_price: float
    leverage: int
    confidence: int

    gross_pnl_pct: float  # 扣费前收益%
    costs_pct: float  # 总成本%
    net_pnl_pct: float  # 净收益%
    net_pnl_usdt: float  # 净收益 USDT

    exit_reason: str  # sl_hit / tp_full / tp_partial / timeout
    mfe_pct: float  # 最大有利偏移%
    mae_pct: float  # 最大不利偏移%
    duration_hours: float
    entry_time: str  # ISO format
    exit_time: str  # ISO format
    signal_source: str  # ai / random / ma_cross / rsi / bollinger
    exit_strategy: str = "fixed"  # fixed / mfe_trailing


_MAX_BARS = 168  # 7 天 × 24h


def simulate_trade(
    signal: dict,
    klines_1h: pd.DataFrame,
    cost_config: CostConfig | None = None,
    position_usdt: float = 1000.0,
    max_bars: int = _MAX_BARS,
    mfe_trailing: bool = False,
    atr_pct: float | None = None,
) -> TradeResult | None:
    """模拟单笔交易

    Args:
        signal: AI/基线信号 dict (symbol, action, entry_price_range, stop_loss,
                take_profit, leverage, confidence, signal_source, timestamp)
        klines_1h: 1h K线 DataFrame (columns: open, high, low, close, volume)
        cost_config: 成本配置，None 用默认
        position_usdt: 仓位金额 (USDT 保证金)
        max_bars: 最大扫描K线数 (默认168 = 7天)

    Returns:
        TradeResult 或 None (数据不足)
    """
    if cost_config is None:
        cost_config = CostConfig()

    # ── 解析信号参数 ──
    entry_range = signal.get("entry_price_range", [])
    if not entry_range or len(entry_range) < 2 or not entry_range[0]:
        return None

    symbol = signal.get("symbol", "UNKNOWN")
    action = signal.get("action", "long")
    is_long = action == "long"
    leverage = signal.get("leverage", 3)
    confidence = signal.get("confidence", 65)
    stop_loss = signal.get("stop_loss")
    take_profits = signal.get("take_profit", [])
    signal_source = signal.get("signal_source", "ai")

    # 入场价 = entry_range 中点 ± 滑点
    entry_mid = (entry_range[0] + entry_range[1]) / 2
    slippage_offset = entry_mid * cost_config.slippage_pct / 100
    entry_price = (
        entry_mid + slippage_offset if is_long else entry_mid - slippage_offset
    )

    if entry_price <= 0:
        return None

    # ── 截取信号之后的 K 线 ──
    df = _slice_klines_after_signal(klines_1h, signal.get("timestamp"))
    if df is None or len(df) < 2:
        return None

    df = df.head(max_bars)

    # ── 解析止盈列表 ──
    tp_levels = _parse_take_profits(take_profits, is_long)

    # ── 逐根扫描 ──
    remaining_ratio = 1.0  # 剩余未平仓比例
    weighted_exit_sum = 0.0  # 加权退出价格
    mfe = 0.0
    mae = 0.0
    exit_reason = "timeout"
    exit_strategy = "fixed"
    exit_bar_idx = len(df) - 1

    # MFE 尾随参数预计算
    _mfe_enabled = mfe_trailing and atr_pct is not None and atr_pct > 0
    _mfe_trigger = atr_pct * 2 if _mfe_enabled else 0.0

    for i, (ts, bar) in enumerate(df.iterrows()):
        high = float(bar["high"])
        low = float(bar["low"])

        # 更新 MFE / MAE
        if is_long:
            favorable = (high - entry_price) / entry_price * 100
            adverse = (entry_price - low) / entry_price * 100
        else:
            favorable = (entry_price - low) / entry_price * 100
            adverse = (high - entry_price) / entry_price * 100
        mfe = max(mfe, favorable)
        mae = max(mae, adverse)

        # ── MFE 自适应尾随止损 ──
        effective_sl = stop_loss
        if _mfe_enabled and mfe >= _mfe_trigger and stop_loss is not None:
            exit_strategy = "mfe_trailing"
            trail_steps = int((mfe - _mfe_trigger) / atr_pct)
            if is_long:
                breakeven_sl = entry_price
                tightened_sl = entry_price * (1 + trail_steps * atr_pct / 100)
                effective_sl = max(breakeven_sl, tightened_sl, stop_loss)
            else:
                breakeven_sl = entry_price
                tightened_sl = entry_price * (1 - trail_steps * atr_pct / 100)
                effective_sl = min(breakeven_sl, tightened_sl, stop_loss)

        # ── 止损检查 (优先) ──
        if effective_sl is not None and remaining_ratio > 0:
            sl_hit = (low <= effective_sl) if is_long else (high >= effective_sl)
            if sl_hit:
                weighted_exit_sum += effective_sl * remaining_ratio
                remaining_ratio = 0.0
                exit_reason = "sl_hit"
                exit_bar_idx = i
                break

        # ── 止盈检查 ──
        if remaining_ratio > 0:
            for tp in tp_levels:
                if tp["triggered"]:
                    continue
                tp_hit = (
                    (high >= tp["price"]) if is_long else (low <= tp["price"])
                )
                if tp_hit:
                    tp["triggered"] = True
                    portion = min(tp["ratio"], remaining_ratio)
                    weighted_exit_sum += tp["price"] * portion
                    remaining_ratio -= portion
                    remaining_ratio = max(0.0, remaining_ratio)

            if remaining_ratio <= 1e-9:
                triggered_count = sum(1 for t in tp_levels if t["triggered"])
                exit_reason = (
                    "tp_full"
                    if triggered_count == len(tp_levels)
                    else "tp_partial"
                )
                exit_bar_idx = i
                break

    # ── 超时或部分止盈后剩余以最后收盘价平仓 ──
    if remaining_ratio > 1e-9:
        last_close = float(df.iloc[exit_bar_idx]["close"])
        weighted_exit_sum += last_close * remaining_ratio
        remaining_ratio = 0.0
        if exit_reason == "timeout":
            exit_bar_idx = len(df) - 1

    exit_price = weighted_exit_sum  # 加权平均退出价

    # ── 计算 PnL ──
    direction = 1 if is_long else -1
    gross_pnl_pct = direction * (exit_price - entry_price) / entry_price * leverage * 100

    duration_hours = max(1.0, float((exit_bar_idx + 1)))
    costs = calc_trade_costs(cost_config, duration_hours, leverage)

    net_pnl_pct = gross_pnl_pct - costs.total_pct
    net_pnl_usdt = position_usdt * leverage * net_pnl_pct / 100

    # ── 时间戳 ──
    entry_time = str(df.index[0])
    exit_time = str(df.index[min(exit_bar_idx, len(df) - 1)])

    return TradeResult(
        symbol=symbol,
        action=action,
        entry_price=round(entry_price, 6),
        exit_price=round(exit_price, 6),
        leverage=leverage,
        confidence=confidence,
        gross_pnl_pct=round(gross_pnl_pct, 4),
        costs_pct=round(costs.total_pct, 4),
        net_pnl_pct=round(net_pnl_pct, 4),
        net_pnl_usdt=round(net_pnl_usdt, 4),
        exit_reason=exit_reason,
        mfe_pct=round(mfe, 4),
        mae_pct=round(mae, 4),
        duration_hours=duration_hours,
        entry_time=entry_time,
        exit_time=exit_time,
        signal_source=signal_source,
        exit_strategy=exit_strategy,
    )


def _slice_klines_after_signal(
    df: pd.DataFrame, timestamp: str | None,
) -> pd.DataFrame | None:
    """截取信号时间之后的 K 线"""
    if df is None or df.empty:
        return None

    if not timestamp:
        return df

    try:
        from datetime import datetime
        sig_ts = datetime.fromisoformat(timestamp)
        idx = df.index
        if idx.tz is None and sig_ts.tzinfo is not None:
            sig_ts = sig_ts.replace(tzinfo=None)
        result = df[idx >= sig_ts]
        return result if not result.empty else df
    except Exception:
        return df


def _parse_take_profits(take_profits: list, is_long: bool) -> list[dict]:
    """解析止盈列表为统一格式

    支持两种输入:
    - [{"price": 100, "ratio": 0.5}, ...]
    - [100, 110] (纯价格，均分比例)
    """
    if not take_profits:
        return []

    levels = []
    for tp in take_profits:
        if isinstance(tp, dict):
            price = tp.get("price")
            ratio = tp.get("ratio", 1.0 / len(take_profits))
        else:
            price = tp
            ratio = 1.0 / len(take_profits)

        if price is not None and price > 0:
            levels.append({"price": price, "ratio": ratio, "triggered": False})

    # 按价格排序: long 从低到高触发，short 从高到低触发
    levels.sort(key=lambda x: x["price"], reverse=not is_long)
    return levels
