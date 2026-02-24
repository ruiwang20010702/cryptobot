"""绩效分析

从交易记录计算胜率、盈亏比、置信度校准等。
"""

from datetime import datetime, timezone, timedelta

from cryptobot.journal.storage import get_all_records


def calc_performance(days: int = 30, *, with_ci: bool = False) -> dict:
    """计算最近 N 天的绩效统计

    Returns:
        {total_signals, entered, closed, win_rate, avg_pnl_pct,
         profit_factor, confidence_calibration, by_direction}
    """
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    all_records = get_all_records()

    # 过滤时间范围
    records = [r for r in all_records if r.timestamp >= cutoff]

    total_signals = len(records)
    entered = [r for r in records if r.status in ("active", "closed")]
    closed = [r for r in records if r.status == "closed"]
    expired = [r for r in records if r.status == "expired"]

    # 基础统计
    wins = [r for r in closed if (r.actual_pnl_pct or 0) > 0]
    losses = [r for r in closed if (r.actual_pnl_pct or 0) <= 0]
    win_rate = len(wins) / len(closed) if closed else 0

    pnl_list = [r.actual_pnl_pct or 0 for r in closed]
    avg_pnl_pct = sum(pnl_list) / len(pnl_list) if pnl_list else 0

    # Profit Factor (capped at 99.9 to avoid inf)
    gross_profit = sum(r.actual_pnl_pct for r in wins if r.actual_pnl_pct)
    gross_loss = abs(sum(r.actual_pnl_pct for r in losses if r.actual_pnl_pct))
    if gross_loss > 0:
        profit_factor = min(gross_profit / gross_loss, 99.9)
    elif gross_profit > 0:
        profit_factor = 99.9
    else:
        profit_factor = 0

    # 置信度校准
    calibration = _calc_confidence_calibration(closed)

    # 按方向
    longs = [r for r in closed if r.action == "long"]
    shorts = [r for r in closed if r.action == "short"]
    by_direction = {
        "long": {
            "count": len(longs),
            "win_rate": len([r for r in longs if (r.actual_pnl_pct or 0) > 0]) / len(longs) if longs else 0,
        },
        "short": {
            "count": len(shorts),
            "win_rate": len([r for r in shorts if (r.actual_pnl_pct or 0) > 0]) / len(shorts) if shorts else 0,
        },
    }

    # 按币种分组
    by_symbol: dict[str, dict] = {}
    for r in closed:
        sym = r.symbol or "UNKNOWN"
        if sym not in by_symbol:
            by_symbol[sym] = {"count": 0, "wins": 0, "total_pnl_pct": 0.0}
        by_symbol[sym]["count"] += 1
        if (r.actual_pnl_pct or 0) > 0:
            by_symbol[sym]["wins"] += 1
        by_symbol[sym]["total_pnl_pct"] += r.actual_pnl_pct or 0

    by_symbol_result = {}
    for sym, stats in by_symbol.items():
        by_symbol_result[sym] = {
            "count": stats["count"],
            "win_rate": round(stats["wins"] / stats["count"], 3) if stats["count"] > 0 else 0,
            "avg_pnl_pct": round(stats["total_pnl_pct"] / stats["count"], 2) if stats["count"] > 0 else 0,
        }

    # P17-A1: 方向分拆校准
    calibration_long = _calc_confidence_calibration(closed, "bullish")
    calibration_short = _calc_confidence_calibration(closed, "bearish")

    result = {
        "period_days": days,
        "total_signals": total_signals,
        "entered": len(entered),
        "closed": len(closed),
        "expired": len(expired),
        "win_rate": round(win_rate, 3),
        "avg_pnl_pct": round(avg_pnl_pct, 2),
        "profit_factor": round(profit_factor, 2),
        "total_pnl_usdt": round(
            sum(r.actual_pnl_usdt or 0 for r in closed), 2,
        ),
        "confidence_calibration": calibration,
        "confidence_calibration_long": calibration_long,
        "confidence_calibration_short": calibration_short,
        "by_direction": by_direction,
        "by_symbol": by_symbol_result,
    }

    if with_ci:
        from cryptobot.backtest.bootstrap import bootstrap_metric_ci

        ci_pnl = [r.actual_pnl_pct or 0 for r in closed]
        ci_results = bootstrap_metric_ci(ci_pnl)
        if ci_results.get("win_rate_ci"):
            result["win_rate_ci"] = {
                "lower": ci_results["win_rate_ci"].lower,
                "upper": ci_results["win_rate_ci"].upper,
            }
        if ci_results.get("avg_pnl_ci"):
            result["avg_pnl_pct_ci"] = {
                "lower": ci_results["avg_pnl_ci"].lower,
                "upper": ci_results["avg_pnl_ci"].upper,
            }
        if ci_results.get("sharpe_ci"):
            result["sharpe_ci"] = {
                "lower": ci_results["sharpe_ci"].lower,
                "upper": ci_results["sharpe_ci"].upper,
            }
        if ci_results.get("profit_factor_ci"):
            result["profit_factor_ci"] = {
                "lower": ci_results["profit_factor_ci"].lower,
                "upper": ci_results["profit_factor_ci"].upper,
            }

    return result


def _calc_confidence_calibration(
    closed_records: list, direction: str | None = None,
) -> dict:
    """计算置信度区间的实际胜率

    Args:
        closed_records: 已平仓记录
        direction: 可选方向过滤 ("bullish"/"bearish")，None 表示全部
    """
    if direction:
        closed_records = [
            r for r in closed_records
            if _normalize_direction(getattr(r, "action", "") or "") == direction
        ]
    buckets = {
        "60-70": {"min": 60, "max": 70, "count": 0, "wins": 0},
        "70-80": {"min": 70, "max": 80, "count": 0, "wins": 0},
        "80-90": {"min": 80, "max": 90, "count": 0, "wins": 0},
        "90+": {"min": 90, "max": 101, "count": 0, "wins": 0},
    }

    for r in closed_records:
        conf = r.confidence or 0
        pnl = r.actual_pnl_pct or 0

        for bucket in buckets.values():
            if bucket["min"] <= conf < bucket["max"]:
                bucket["count"] += 1
                if pnl > 0:
                    bucket["wins"] += 1
                break

    result = {}
    for name, b in buckets.items():
        result[name] = {
            "count": b["count"],
            "actual_win_rate": round(b["wins"] / b["count"], 3) if b["count"] > 0 else None,
        }

    return result


def _normalize_direction(direction: str) -> str:
    """统一方向名称: long/buy/up → bullish, short/sell/down → bearish"""
    d = direction.lower().strip()
    if d in ("bullish", "long", "buy", "up"):
        return "bullish"
    if d in ("bearish", "short", "sell", "down"):
        return "bearish"
    return "neutral"


def calc_analyst_accuracy(days: int = 30) -> dict:
    """按分析师角色计算投票方向与交易结果的准确率

    Returns:
        {role: {total, correct, accuracy}, ...}
    """
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    all_records = get_all_records()

    closed = [
        r for r in all_records
        if r.status == "closed" and r.timestamp >= cutoff
        and r.analyst_votes and r.actual_pnl_pct is not None
    ]

    role_stats: dict[str, dict] = {}

    for r in closed:
        is_win = r.actual_pnl_pct > 0
        # 判断"正确"：分析师方向与交易结果一致
        # long + bullish = 正确（盈利时）；short + bearish = 正确（盈利时）
        # 简化逻辑：投票方向与 action 一致且盈利，或方向相反且亏损
        action_direction = "bullish" if r.action == "long" else "bearish"

        for role, vote in r.analyst_votes.items():
            if role not in role_stats:
                role_stats[role] = {"total": 0, "correct": 0}

            role_stats[role]["total"] += 1
            normalized_vote = _normalize_direction(vote)
            vote_agrees = (normalized_vote == action_direction)
            if (vote_agrees and is_win) or (not vote_agrees and not is_win):
                role_stats[role]["correct"] += 1

    result = {}
    for role, stats in role_stats.items():
        result[role] = {
            "total": stats["total"],
            "correct": stats["correct"],
            "accuracy": round(stats["correct"] / stats["total"], 3) if stats["total"] > 0 else 0,
        }

    return result


def build_performance_summary(days: int = 30) -> str:
    """生成可注入 TRADER/RISK_MANAGER prompt 的绩效摘要文本

    无交易记录时返回空字符串（不污染 prompt）。
    """
    perf = calc_performance(days)

    if perf["closed"] < 10:
        return ""  # 样本不足，不注入

    lines = [
        f"### 近期表现参考 (近 {days} 天)",
        f"- 总信号: {perf['total_signals']} 个, "
        f"已入场: {perf['entered']}, "
        f"已平仓: {perf['closed']}",
        f"- 胜率: {perf['win_rate'] * 100:.1f}%, "
        f"平均盈亏: {perf['avg_pnl_pct']:+.1f}%",
        f"- 盈亏比 (Profit Factor): {perf['profit_factor']}",
        f"- 累计盈亏: {perf['total_pnl_usdt']:+.0f} USDT",
    ]

    # 方向胜率
    by_dir = perf.get("by_direction", {})
    long_info = by_dir.get("long", {})
    short_info = by_dir.get("short", {})
    if long_info.get("count", 0) > 0 or short_info.get("count", 0) > 0:
        lines.append(
            f"- 多单: {long_info.get('count', 0)} 笔 "
            f"胜率 {long_info.get('win_rate', 0) * 100:.0f}% | "
            f"空单: {short_info.get('count', 0)} 笔 "
            f"胜率 {short_info.get('win_rate', 0) * 100:.0f}%"
        )

    # P17-A2: 做多绩效预警
    if long_info.get("count", 0) >= 5:
        long_wr = long_info.get("win_rate", 0)
        if long_wr < 0.4:
            lines.append(
                f"- **做多预警**: 近 {days}d 做多胜率仅 {long_wr * 100:.0f}%，"
                "建议提高做多置信度要求或减少做多频率"
            )

    # 置信度校准偏差
    cal = perf.get("confidence_calibration", {})
    cal_notes = []
    for bucket_name, bucket_data in cal.items():
        if bucket_data["count"] >= 3 and bucket_data["actual_win_rate"] is not None:
            mid = {"60-70": 65, "70-80": 75, "80-90": 85, "90+": 95}.get(bucket_name, 0)
            actual = bucket_data["actual_win_rate"] * 100
            diff = actual - mid
            if abs(diff) > 10:
                bias = "偏乐观" if diff < -10 else "偏保守"
                cal_notes.append(
                    f"confidence {bucket_name}: 实际胜率 {actual:.0f}% ({bias})"
                )
    if cal_notes:
        lines.append("- 置信度校准: " + "; ".join(cal_notes))

    # 分析师准确率（样本 >= 10 时展示）
    if perf["closed"] >= 30:
        analyst_acc = calc_analyst_accuracy(days)
        acc_parts = []
        for role in ("technical", "onchain", "sentiment", "fundamental"):
            info = analyst_acc.get(role)
            if info and info["total"] >= 15:
                acc_parts.append(f"{role}: {info['accuracy'] * 100:.0f}%")
        if acc_parts:
            lines.append(f"- 分析师准确率: {', '.join(acc_parts)}")

    # 最近 5 笔已平仓交易
    all_records = get_all_records()
    closed_records = sorted(
        [r for r in all_records if r.status == "closed" and r.actual_pnl_pct is not None],
        key=lambda r: r.timestamp,
        reverse=True,
    )[:5]
    if closed_records:
        recent = ", ".join(
            f"{r.symbol} {r.actual_pnl_pct:+.1f}%" for r in closed_records
        )
        lines.append(f"- 最近 {len(closed_records)} 笔: {recent}")

    lines.append("")
    return "\n".join(lines)
