"""Edge 仪表盘: 期望值/SQN/R分布/Regime分组/衰减检测

纯 Python 实现，从 journal records 计算交易系统的 Edge 指标。
"""

import math
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone


@dataclass(frozen=True)
class EdgeMetrics:
    """Edge 指标集合"""

    expectancy_pct: float  # 期望值 = wr*avg_win - (1-wr)*avg_loss
    edge_ratio: float  # avg_win / avg_loss
    sqn: float  # System Quality Number = sqrt(N) * avg_pnl / std_pnl
    r_distribution: dict  # R 倍数分布
    regime_edge: dict  # 按 regime 分组
    recent_vs_baseline: dict  # 7d vs 30d 对比


def _filter_closed(days: int) -> list:
    """获取最近 N 天的已平仓记录"""
    from cryptobot.journal.storage import get_all_records

    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    all_records = get_all_records()
    return [
        r
        for r in all_records
        if r.status == "closed"
        and r.actual_pnl_pct is not None
        and r.timestamp >= cutoff
    ]


def _calc_expectancy(records: list) -> float:
    """计算期望值: wr * avg_win - (1-wr) * avg_loss"""
    if not records:
        return 0.0
    wins = [r for r in records if r.actual_pnl_pct > 0]
    losses = [r for r in records if r.actual_pnl_pct <= 0]
    wr = len(wins) / len(records)
    avg_win = sum(r.actual_pnl_pct for r in wins) / len(wins) if wins else 0.0
    avg_loss = (
        abs(sum(r.actual_pnl_pct for r in losses) / len(losses)) if losses else 0.0
    )
    return round(wr * avg_win - (1 - wr) * avg_loss, 4)


def _calc_edge_ratio(records: list) -> float:
    """计算 edge_ratio: avg_win / avg_loss"""
    wins = [r for r in records if r.actual_pnl_pct > 0]
    losses = [r for r in records if r.actual_pnl_pct <= 0]
    avg_win = sum(r.actual_pnl_pct for r in wins) / len(wins) if wins else 0.0
    avg_loss = (
        abs(sum(r.actual_pnl_pct for r in losses) / len(losses)) if losses else 0.0
    )
    if avg_loss == 0:
        return 0.0
    return round(avg_win / avg_loss, 4)


def _calc_sqn(records: list) -> float:
    """计算 SQN: sqrt(N) * mean_pnl / std_pnl"""
    if len(records) < 2:
        return 0.0
    pnl_list = [r.actual_pnl_pct for r in records]
    n = len(pnl_list)
    mean = sum(pnl_list) / n
    variance = sum((x - mean) ** 2 for x in pnl_list) / (n - 1)
    std = math.sqrt(variance)
    if std == 0:
        return 0.0
    return round(math.sqrt(n) * mean / std, 4)


def _calc_r_distribution(records: list) -> dict:
    """计算 R 倍数分布

    R = actual_pnl_pct / stop_loss_distance
    stop_loss_distance 从 entry_price_range 中点和 stop_loss 计算。
    无止损数据时跳过该记录。
    """
    buckets = {
        "<-3R": 0,
        "-3R~-2R": 0,
        "-2R~-1R": 0,
        "-1R~0R": 0,
        "0R~1R": 0,
        "1R~2R": 0,
        "2R~3R": 0,
        ">3R": 0,
    }

    for r in records:
        sl_distance = _get_sl_distance(r)
        if sl_distance is None or sl_distance == 0:
            continue
        r_multiple = r.actual_pnl_pct / sl_distance
        bucket = _r_to_bucket(r_multiple)
        buckets[bucket] += 1

    return buckets


def _get_sl_distance(record) -> float | None:
    """计算止损距离（百分比）"""
    if not record.stop_loss:
        return None
    entry_range = record.entry_price_range or []
    if not entry_range:
        if record.actual_entry_price:
            entry = record.actual_entry_price
        else:
            return None
    else:
        entry = sum(entry_range) / len(entry_range)

    if entry == 0:
        return None

    return abs(entry - record.stop_loss) / entry * 100


def _r_to_bucket(r_multiple: float) -> str:
    """将 R 倍数映射到分桶"""
    if r_multiple < -3:
        return "<-3R"
    if r_multiple < -2:
        return "-3R~-2R"
    if r_multiple < -1:
        return "-2R~-1R"
    if r_multiple < 0:
        return "-1R~0R"
    if r_multiple < 1:
        return "0R~1R"
    if r_multiple < 2:
        return "1R~2R"
    if r_multiple < 3:
        return "2R~3R"
    return ">3R"


def _calc_regime_edge(records: list) -> dict:
    """按 regime 分组计算 Edge"""
    groups: dict[str, list] = {}
    for r in records:
        regime = r.regime_name or "unknown"
        groups.setdefault(regime, []).append(r)

    result = {}
    for regime, group in groups.items():
        wins = [r for r in group if r.actual_pnl_pct > 0]
        wr = len(wins) / len(group) if group else 0
        result[regime] = {
            "count": len(group),
            "win_rate": round(wr, 4),
            "expectancy": _calc_expectancy(group),
            "edge_ratio": _calc_edge_ratio(group),
        }
    return result


def _calc_period_summary(records: list) -> dict:
    """计算某段时间的摘要"""
    if not records:
        return {"count": 0, "expectancy": 0.0, "win_rate": 0.0, "avg_pnl": 0.0}
    wins = [r for r in records if r.actual_pnl_pct > 0]
    pnl_list = [r.actual_pnl_pct for r in records]
    return {
        "count": len(records),
        "expectancy": _calc_expectancy(records),
        "win_rate": round(len(wins) / len(records), 4),
        "avg_pnl": round(sum(pnl_list) / len(pnl_list), 4),
    }


def calc_edge(days: int = 30) -> EdgeMetrics:
    """从 journal records 计算 Edge 指标

    1. 从 storage.get_all_records() 获取已平仓记录（最近 days 天）
    2. 计算 expectancy, edge_ratio, sqn
    3. R 倍数分布（R = actual_pnl / stop_loss_distance）
    4. 按 regime 分组
    5. 7d vs 30d 对比
    """
    closed = _filter_closed(days)

    # 7d vs 全周期对比
    cutoff_7d = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()
    recent_7d = [r for r in closed if r.timestamp >= cutoff_7d]
    baseline = closed  # 全周期 (days 天)

    recent_summary = _calc_period_summary(recent_7d)
    baseline_summary = _calc_period_summary(baseline)

    # 计算变化
    change = {}
    for key in ("expectancy", "win_rate", "avg_pnl"):
        base_val = baseline_summary[key]
        recent_val = recent_summary[key]
        if base_val != 0:
            change[key] = round((recent_val - base_val) / abs(base_val) * 100, 2)
        else:
            change[key] = 0.0

    return EdgeMetrics(
        expectancy_pct=_calc_expectancy(closed),
        edge_ratio=_calc_edge_ratio(closed),
        sqn=_calc_sqn(closed),
        r_distribution=_calc_r_distribution(closed),
        regime_edge=_calc_regime_edge(closed),
        recent_vs_baseline={
            "recent_7d": recent_summary,
            "baseline_30d": baseline_summary,
            "change": change,
        },
    )


def calc_edge_trend(lookback_days: int = 90, window: int = 7) -> list[dict]:
    """滚动窗口 Edge 趋势（每 window 天一个点）

    Returns: [{"date": "2026-01-01", "expectancy": 1.2, "sqn": 1.5, "win_rate": 0.6}, ...]
    """
    closed = _filter_closed(lookback_days)
    if not closed:
        return []

    # 按时间排序
    closed.sort(key=lambda r: r.timestamp)

    now = datetime.now(timezone.utc)
    points = []
    # 从 lookback_days 前开始，每 window 天一个点
    start = now - timedelta(days=lookback_days)

    step = 0
    while True:
        window_end = start + timedelta(days=window * (step + 1))
        if window_end > now:
            break
        window_start = start + timedelta(days=window * step)

        w_start_iso = window_start.isoformat()
        w_end_iso = window_end.isoformat()
        window_records = [
            r for r in closed if w_start_iso <= r.timestamp < w_end_iso
        ]

        if window_records:
            wins = [r for r in window_records if r.actual_pnl_pct > 0]
            points.append({
                "date": window_end.strftime("%Y-%m-%d"),
                "expectancy": _calc_expectancy(window_records),
                "sqn": _calc_sqn(window_records),
                "win_rate": round(
                    len(wins) / len(window_records), 4
                ),
            })
        step += 1

    return points


def detect_edge_decay(short_days: int = 7, long_days: int = 30) -> dict:
    """检测 Edge 衰减

    Returns: {"decaying": bool, "short_expectancy": float, "long_expectancy": float,
              "change_pct": float, "warning": str}
    """
    short_records = _filter_closed(short_days)
    long_records = _filter_closed(long_days)

    short_exp = _calc_expectancy(short_records)
    long_exp = _calc_expectancy(long_records)

    if long_exp == 0:
        change_pct = 0.0
    else:
        change_pct = round((short_exp - long_exp) / abs(long_exp) * 100, 2)

    # 衰减判定: 短期期望值比长期低 30% 以上，或短期为负
    decaying = (change_pct < -30) or (short_exp < 0 and long_exp > 0)

    warning = ""
    if decaying:
        if short_exp < 0:
            warning = f"Edge 衰减: 近 {short_days}d 期望值为负 ({short_exp:.2f}%)"
        else:
            warning = (
                f"Edge 衰减: 近 {short_days}d 期望值下降 {abs(change_pct):.0f}%"
            )

    return {
        "decaying": decaying,
        "short_expectancy": short_exp,
        "long_expectancy": long_exp,
        "change_pct": change_pct,
        "warning": warning,
    }
