"""信号回测评估器

对已平仓信号进行多维度复盘分析:
- 方向准确率、盈亏比、置信度校准
- 按币种/方向/杠杆分组统计
- 信号 vs 实际 K 线走势对比（最大有利/不利偏移）
"""

import logging

from cryptobot.journal.models import SignalRecord
from cryptobot.journal.storage import get_all_records

logger = logging.getLogger(__name__)


def evaluate_signals(days: int = 30) -> dict:
    """评估最近 N 天的所有已平仓信号

    Returns:
        {overview, by_symbol, by_direction, by_leverage_tier,
         risk_reward, streak}
    """
    from datetime import datetime, timezone, timedelta

    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    all_records = get_all_records()
    closed = [
        r for r in all_records
        if r.status == "closed" and r.timestamp >= cutoff
        and r.actual_pnl_pct is not None
    ]

    if not closed:
        return {"overview": {"total": 0}, "by_symbol": {}, "by_direction": {},
                "by_leverage_tier": {}, "risk_reward": {}, "streak": {}}

    # ── 总览 ──
    wins = [r for r in closed if r.actual_pnl_pct > 0]
    losses = [r for r in closed if r.actual_pnl_pct <= 0]
    pnl_list = [r.actual_pnl_pct for r in closed]

    overview = {
        "total": len(closed),
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": round(len(wins) / len(closed), 3),
        "avg_pnl_pct": round(sum(pnl_list) / len(pnl_list), 2),
        "best_trade_pct": round(max(pnl_list), 2),
        "worst_trade_pct": round(min(pnl_list), 2),
        "total_pnl_usdt": round(sum(r.actual_pnl_usdt or 0 for r in closed), 2),
    }

    # ── 按币种 ──
    by_symbol = _group_stats(closed, key=lambda r: r.symbol)

    # ── 按方向 ──
    by_direction = _group_stats(closed, key=lambda r: r.action)

    # ── 按杠杆档位 ──
    def _lev_tier(r: SignalRecord) -> str:
        lev = r.leverage or 1
        if lev <= 2:
            return "1-2x"
        elif lev <= 3:
            return "3x"
        else:
            return "4x+"
    by_leverage = _group_stats(closed, key=_lev_tier)

    # ── 实际盈亏比 vs 计划盈亏比 ──
    risk_reward = _calc_risk_reward(closed)

    # ── 连续胜败 ──
    streak = _calc_streak(closed)

    return {
        "period_days": days,
        "overview": overview,
        "by_symbol": by_symbol,
        "by_direction": by_direction,
        "by_leverage_tier": by_leverage,
        "risk_reward": risk_reward,
        "streak": streak,
    }


def _group_stats(records: list[SignalRecord], key) -> dict:
    """按 key 分组计算统计量"""
    groups: dict[str, list] = {}
    for r in records:
        k = key(r)
        groups.setdefault(k, []).append(r)

    result = {}
    for name, group in sorted(groups.items()):
        wins = [r for r in group if r.actual_pnl_pct > 0]
        pnl_list = [r.actual_pnl_pct for r in group]
        result[name] = {
            "count": len(group),
            "wins": len(wins),
            "win_rate": round(len(wins) / len(group), 3),
            "avg_pnl_pct": round(sum(pnl_list) / len(pnl_list), 2),
            "total_pnl_usdt": round(sum(r.actual_pnl_usdt or 0 for r in group), 2),
        }
    return result


def _calc_risk_reward(closed: list[SignalRecord]) -> dict:
    """分析实际盈亏比"""
    avg_win = 0.0
    avg_loss = 0.0
    wins = [r for r in closed if r.actual_pnl_pct > 0]
    losses = [r for r in closed if r.actual_pnl_pct <= 0]

    if wins:
        avg_win = sum(r.actual_pnl_pct for r in wins) / len(wins)
    if losses:
        avg_loss = abs(sum(r.actual_pnl_pct for r in losses) / len(losses))

    actual_rr = round(avg_win / avg_loss, 2) if avg_loss > 0 else float("inf")

    return {
        "avg_win_pct": round(avg_win, 2),
        "avg_loss_pct": round(avg_loss, 2),
        "actual_risk_reward": actual_rr if actual_rr != float("inf") else "inf",
    }


def _calc_streak(closed: list[SignalRecord]) -> dict:
    """计算最大连胜/连败"""
    sorted_records = sorted(closed, key=lambda r: r.timestamp)

    max_win_streak = 0
    max_loss_streak = 0
    current_win = 0
    current_loss = 0

    for r in sorted_records:
        if r.actual_pnl_pct > 0:
            current_win += 1
            current_loss = 0
        else:
            current_loss += 1
            current_win = 0
        max_win_streak = max(max_win_streak, current_win)
        max_loss_streak = max(max_loss_streak, current_loss)

    return {
        "max_consecutive_wins": max_win_streak,
        "max_consecutive_losses": max_loss_streak,
    }


def replay_signal(record: SignalRecord) -> dict | None:
    """对单个信号进行 K 线复盘

    加载信号时间段的 K 线数据，计算:
    - 是否进入 entry_range
    - 止损是否被触发
    - 各止盈级别是否被触发
    - 最大有利/不利偏移 (MAE/MFE)

    Returns:
        复盘结果 dict，无 K 线数据时返回 None
    """
    from datetime import datetime

    if not record.entry_price_range or len(record.entry_price_range) != 2:
        return None

    try:
        from cryptobot.indicators.calculator import load_klines
        df = load_klines(record.symbol, "1h")
    except Exception:
        return None

    entry_lo, entry_hi = record.entry_price_range
    if not entry_lo or not entry_hi:
        return None

    entry_mid = (entry_lo + entry_hi) / 2
    is_long = record.action == "long"

    # 截取信号之后的 K 线 (最多 7 天 = 168 根 1h)
    try:
        signal_ts = datetime.fromisoformat(record.timestamp)
        df_after = df[df.index >= signal_ts].head(168)
    except Exception:
        # 如果时间格式有问题，用最后 168 根
        df_after = df.tail(168)

    if df_after.empty:
        return None

    highs = df_after["high"].values
    lows = df_after["low"].values

    # 最大有利偏移 (MFE) 和最大不利偏移 (MAE)
    if is_long:
        max_high = float(max(highs))
        min_low = float(min(lows))
        mfe_pct = (max_high - entry_mid) / entry_mid * 100
        mae_pct = (entry_mid - min_low) / entry_mid * 100
    else:
        max_high = float(max(highs))
        min_low = float(min(lows))
        mfe_pct = (entry_mid - min_low) / entry_mid * 100
        mae_pct = (max_high - entry_mid) / entry_mid * 100

    # 止损是否被触发
    sl_hit = False
    if record.stop_loss:
        if is_long:
            sl_hit = min_low <= record.stop_loss
        else:
            sl_hit = max_high >= record.stop_loss

    # 止盈触发数
    tp_hits = 0
    for tp in record.take_profit:
        tp_price = tp.get("price") if isinstance(tp, dict) else tp
        if tp_price is None:
            continue
        if is_long and max_high >= tp_price:
            tp_hits += 1
        elif not is_long and min_low <= tp_price:
            tp_hits += 1

    return {
        "symbol": record.symbol,
        "action": record.action,
        "entry_mid": round(entry_mid, 2),
        "mfe_pct": round(mfe_pct, 2),
        "mae_pct": round(mae_pct, 2),
        "sl_hit": sl_hit,
        "tp_hits": tp_hits,
        "tp_total": len(record.take_profit),
        "bars_analyzed": len(df_after),
    }
