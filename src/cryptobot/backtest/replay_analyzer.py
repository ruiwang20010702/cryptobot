"""回放分析器 — 置信度分层 + 方向偏差诊断 + 回撤控制模拟

纯函数设计，所有函数接收 list[TradeResult] 返回 dict。

入口: analyze_replay(trades) → AnalysisReport
"""

import math
from collections import defaultdict
from dataclasses import dataclass

from cryptobot.backtest.trade_simulator import TradeResult

# ── 置信度分桶定义 ───────────────────────────────────────────────────────

CONFIDENCE_BUCKETS = [(55, 64, "55-64"), (65, 74, "65-74"), (75, 100, "75+")]


@dataclass(frozen=True)
class AnalysisReport:
    """回放分析报告"""

    confidence_buckets: dict
    direction_analysis: dict
    drawdown_simulation: dict
    symbol_heatmap: dict
    time_distribution: dict
    recommendations: list[str]


# ── 主入口 ────────────────────────────────────────────────────────────────


def analyze_replay(
    trades: list[TradeResult],
    daily_limits: list[float] | None = None,
) -> AnalysisReport:
    """主入口：编排全部分析

    Args:
        trades: TradeResult 列表
        daily_limits: 每日亏损限额百分比列表，默认 [3.0, 5.0, 10.0]
    """
    conf = _confidence_stratify(trades)
    direction = _direction_analysis(trades)
    dd_sim = _drawdown_simulation(trades, daily_limits)
    heatmap = _symbol_cross_table(trades)
    time_dist = _time_distribution(trades)
    recs = _generate_recommendations(conf, direction, dd_sim)

    return AnalysisReport(
        confidence_buckets=conf,
        direction_analysis=direction,
        drawdown_simulation=dd_sim,
        symbol_heatmap=heatmap,
        time_distribution=time_dist,
        recommendations=recs,
    )


# ── 置信度分层 ────────────────────────────────────────────────────────────


def _confidence_stratify(trades: list[TradeResult]) -> dict:
    """按置信度分桶统计胜率/盈亏比/平均PnL"""
    buckets: dict[str, list[TradeResult]] = {label: [] for _, _, label in CONFIDENCE_BUCKETS}

    for t in trades:
        for lo, hi, label in CONFIDENCE_BUCKETS:
            if lo <= t.confidence <= hi:
                buckets[label].append(t)
                break

    result = {}
    for label, group in buckets.items():
        result[label] = _bucket_stats(group)

    return result


def _bucket_stats(group: list[TradeResult]) -> dict:
    """单桶统计"""
    if not group:
        return {
            "count": 0, "win_rate": 0.0, "avg_pnl_pct": 0.0,
            "profit_factor": 0.0, "avg_confidence": 0, "avg_leverage": 0,
            "avg_duration_hours": 0.0,
        }

    n = len(group)
    wins = sum(1 for t in group if t.net_pnl_pct > 0)
    pnls = [t.net_pnl_pct for t in group]

    gross_profit = sum(p for p in pnls if p > 0)
    gross_loss = abs(sum(p for p in pnls if p <= 0))
    pf = gross_profit / gross_loss if gross_loss > 0 else float("inf") if gross_profit > 0 else 0.0

    return {
        "count": n,
        "win_rate": round(wins / n, 4),
        "avg_pnl_pct": round(sum(pnls) / n, 4),
        "profit_factor": round(pf, 4) if pf != float("inf") else float("inf"),
        "avg_confidence": round(sum(t.confidence for t in group) / n),
        "avg_leverage": round(sum(t.leverage for t in group) / n),
        "avg_duration_hours": round(sum(t.duration_hours for t in group) / n, 1),
    }


# ── 方向分析 ──────────────────────────────────────────────────────────────


def _direction_group_stats(group: list[TradeResult]) -> dict:
    """方向分组统计: count/win_rate/avg_pnl_pct/total_pnl_usdt"""
    if not group:
        return {"count": 0, "win_rate": 0.0, "avg_pnl_pct": 0.0, "total_pnl_usdt": 0.0}
    n = len(group)
    wins = sum(1 for t in group if t.net_pnl_pct > 0)
    return {
        "count": n,
        "win_rate": round(wins / n, 4),
        "avg_pnl_pct": round(sum(t.net_pnl_pct for t in group) / n, 4),
        "total_pnl_usdt": round(sum(t.net_pnl_usdt for t in group), 2),
    }


# P17 置信度×方向分桶
_CONF_DIR_BUCKETS = [(60, 69, "60-69"), (70, 79, "70-79"), (80, 100, "80+")]

# P17 杠杆×方向分桶
_LEV_DIR_BUCKETS = [(1, 1, "1x"), (2, 2, "2x"), (3, 5, "3-5x")]


def _direction_analysis(trades: list[TradeResult]) -> dict:
    """方向分布 + 月×方向交叉表 + 置信度×方向 + 杠杆×方向"""
    if not trades:
        return {
            "summary": {}, "monthly_trend": {},
            "confidence_direction": {}, "leverage_direction": {},
        }

    by_dir: dict[str, list[TradeResult]] = defaultdict(list)
    for t in trades:
        by_dir[t.action].append(t)

    summary = {}
    for action, group in sorted(by_dir.items()):
        n = len(group)
        wins = sum(1 for t in group if t.net_pnl_pct > 0)
        pnls = [t.net_pnl_pct for t in group]
        summary[action] = {
            "count": n,
            "ratio": round(n / len(trades), 4),
            "win_rate": round(wins / n, 4),
            "avg_pnl_pct": round(sum(pnls) / n, 4),
            "total_pnl_usdt": round(sum(t.net_pnl_usdt for t in group), 2),
        }

    # P17: 月×方向交叉表 (扩展为 count/win_rate/avg_pnl_pct/total_pnl_usdt)
    monthly_groups: dict[str, dict[str, list[TradeResult]]] = defaultdict(
        lambda: defaultdict(list),
    )
    for t in trades:
        month = t.entry_time[:7] if len(t.entry_time) >= 7 else "unknown"
        monthly_groups[month][t.action].append(t)

    monthly_trend = {
        m: {action: _direction_group_stats(group) for action, group in sorted(dirs.items())}
        for m, dirs in sorted(monthly_groups.items())
    }

    # P17: 置信度×方向交叉表
    conf_dir = _cross_table_by_buckets(trades, _CONF_DIR_BUCKETS, key_fn=lambda t: t.confidence)

    # P17: 杠杆×方向交叉表
    lev_dir = _cross_table_by_buckets(trades, _LEV_DIR_BUCKETS, key_fn=lambda t: t.leverage)

    # 偏差度量: 以 long/short 两方向为基准 (均衡=0.5)
    max_ratio = max((s["ratio"] for s in summary.values()), default=0)
    direction_bias = round(max_ratio - 0.5, 4) if summary else 0.0

    return {
        "summary": summary,
        "monthly_trend": monthly_trend,
        "confidence_direction": conf_dir,
        "leverage_direction": lev_dir,
        "direction_bias": direction_bias,
        "dominant_direction": max(summary, key=lambda k: summary[k]["count"]) if summary else "",
    }


def _cross_table_by_buckets(
    trades: list[TradeResult],
    buckets: list[tuple[int, int, str]],
    key_fn,
) -> dict:
    """按分桶 × 方向生成交叉表"""
    matrix: dict[str, dict[str, list[TradeResult]]] = {
        label: defaultdict(list) for _, _, label in buckets
    }
    for t in trades:
        val = key_fn(t)
        for lo, hi, label in buckets:
            if lo <= val <= hi:
                matrix[label][t.action].append(t)
                break

    return {
        label: {action: _direction_group_stats(group) for action, group in sorted(dirs.items())}
        for label, dirs in matrix.items()
        if dirs  # 跳过空桶
    }


# ── 回撤控制模拟 ──────────────────────────────────────────────────────────


def _drawdown_simulation(
    trades: list[TradeResult],
    daily_limits: list[float] | None = None,
) -> dict:
    """回撤控制模拟：按 entry_time 排序逐笔 re-walk"""
    if not trades:
        return {}

    if daily_limits is None:
        daily_limits = [3.0, 5.0, 10.0]

    sorted_trades = sorted(trades, key=lambda t: t.entry_time)

    results = {}
    # 1. 基线
    results["no_control"] = _simulate_walk(sorted_trades, control=None)

    # 2. 每日亏损限额
    for limit in daily_limits:
        results[f"daily_limit_{limit}pct"] = _simulate_walk(
            sorted_trades, control=("daily_loss", limit),
        )

    # 3. 动态杠杆
    results["dynamic_leverage"] = _simulate_walk(
        sorted_trades, control=("dynamic_leverage", None),
    )

    return results


def _simulate_walk(
    trades: list[TradeResult],
    control: tuple[str, float | None] | None,
    initial_equity: float = 10000.0,
) -> dict:
    """逐笔模拟 walk，应用控制策略"""
    equity = initial_equity
    peak = equity
    max_dd = 0.0
    daily_pnl: dict[str, float] = defaultdict(float)
    trades_taken = 0
    trades_skipped = 0
    returns: list[float] = []

    for t in trades:
        day_key = t.entry_time[:10] if len(t.entry_time) >= 10 else ""

        # 检查控制规则
        if control is not None:
            ctrl_type, ctrl_val = control

            if ctrl_type == "daily_loss" and ctrl_val is not None:
                if daily_pnl[day_key] <= -ctrl_val:
                    trades_skipped += 1
                    continue

            elif ctrl_type == "dynamic_leverage":
                # 按当前回撤深度缩放 PnL
                dd_pct = (peak - equity) / peak * 100 if peak > 0 else 0
                if dd_pct > 40:
                    scale = 0.25
                elif dd_pct > 20:
                    scale = 0.5
                else:
                    scale = 1.0

                adjusted_pnl = t.net_pnl_pct * scale
                equity = equity * (1 + adjusted_pnl / 100)
                peak = max(peak, equity)
                dd = (peak - equity) / peak * 100 if peak > 0 else 0
                max_dd = max(max_dd, dd)
                daily_pnl[day_key] += adjusted_pnl
                trades_taken += 1
                returns.append(adjusted_pnl)
                continue

        # 默认执行
        equity = equity * (1 + t.net_pnl_pct / 100)
        peak = max(peak, equity)
        dd = (peak - equity) / peak * 100 if peak > 0 else 0
        max_dd = max(max_dd, dd)
        daily_pnl[day_key] += t.net_pnl_pct
        trades_taken += 1
        returns.append(t.net_pnl_pct)

    total_return = (equity - initial_equity) / initial_equity * 100
    sharpe = _simple_sharpe(returns)
    calmar = total_return / max_dd if max_dd > 0 else 0.0

    return {
        "total_return_pct": round(total_return, 4),
        "max_drawdown_pct": round(max_dd, 4),
        "sharpe": round(sharpe, 4),
        "calmar": round(calmar, 4),
        "final_equity": round(equity, 2),
        "trades_taken": trades_taken,
        "trades_skipped": trades_skipped,
    }


def _simple_sharpe(returns: list[float]) -> float:
    """简化 Sharpe 计算"""
    if len(returns) < 2:
        return 0.0
    mean_r = sum(returns) / len(returns)
    variance = sum((r - mean_r) ** 2 for r in returns) / (len(returns) - 1)
    std_r = math.sqrt(variance) if variance > 0 else 0.0
    if std_r == 0:
        return 0.0
    return mean_r / std_r * math.sqrt(len(returns))


# ── 币种×方向交叉表 ──────────────────────────────────────────────────────


def _symbol_cross_table(trades: list[TradeResult]) -> dict:
    """币种×方向 交叉表 (count + win_rate)"""
    if not trades:
        return {}

    matrix: dict[str, dict[str, list[TradeResult]]] = defaultdict(
        lambda: defaultdict(list),
    )
    for t in trades:
        matrix[t.symbol][t.action].append(t)

    result = {}
    for symbol in sorted(matrix):
        result[symbol] = {}
        for action in sorted(matrix[symbol]):
            group = matrix[symbol][action]
            n = len(group)
            wins = sum(1 for t in group if t.net_pnl_pct > 0)
            result[symbol][action] = {
                "count": n,
                "win_rate": round(wins / n, 4) if n else 0,
                "avg_pnl_pct": round(
                    sum(t.net_pnl_pct for t in group) / n, 4,
                ) if n else 0,
            }

    return result


# ── 时间分布 ──────────────────────────────────────────────────────────────


def _time_distribution(trades: list[TradeResult]) -> dict:
    """按月/按周统计信号数量和胜率"""
    if not trades:
        return {"monthly": {}, "weekly": {}}

    monthly: dict[str, list[TradeResult]] = defaultdict(list)
    weekly: dict[str, list[TradeResult]] = defaultdict(list)

    for t in trades:
        month = t.entry_time[:7] if len(t.entry_time) >= 7 else "unknown"
        monthly[month].append(t)

        # ISO weekday: 提取日期计算周
        if len(t.entry_time) >= 10:
            try:
                from datetime import datetime
                dt = datetime.fromisoformat(t.entry_time[:19])
                week_key = f"{dt.isocalendar()[0]}-W{dt.isocalendar()[1]:02d}"
                weekly[week_key].append(t)
            except (ValueError, TypeError):
                pass

    return {
        "monthly": {
            m: _period_stats(group) for m, group in sorted(monthly.items())
        },
        "weekly": {
            w: _period_stats(group) for w, group in sorted(weekly.items())
        },
    }


def _period_stats(group: list[TradeResult]) -> dict:
    """时间段统计"""
    n = len(group)
    wins = sum(1 for t in group if t.net_pnl_pct > 0)
    return {
        "count": n,
        "win_rate": round(wins / n, 4) if n else 0,
        "avg_pnl_pct": round(
            sum(t.net_pnl_pct for t in group) / n, 4,
        ) if n else 0,
        "total_pnl_pct": round(sum(t.net_pnl_pct for t in group), 4),
    }


# ── 建议生成 ──────────────────────────────────────────────────────────────


def _generate_recommendations(
    confidence: dict,
    direction: dict,
    drawdown: dict,
) -> list[str]:
    """基于分析结果生成可执行建议"""
    recs: list[str] = []

    # 1. 置信度分层建议
    low_bucket = confidence.get("55-64", {})
    if low_bucket.get("count", 0) > 0 and low_bucket.get("win_rate", 0) < 0.45:
        recs.append(
            f"低置信度桶(55-64)胜率仅 {low_bucket['win_rate']:.0%}，"
            "建议提高最低置信度阈值至 65 或降低该桶杠杆"
        )

    high_bucket = confidence.get("75+", {})
    if high_bucket.get("count", 0) > 0 and high_bucket.get("win_rate", 0) > 0.6:
        recs.append(
            f"高置信度桶(75+)胜率 {high_bucket['win_rate']:.0%}，"
            "可适当提高该桶仓位比例"
        )

    # 2. 方向偏差建议
    bias = direction.get("direction_bias", 0)
    dominant = direction.get("dominant_direction", "")
    if bias > 0.3 and dominant:
        summary = direction.get("summary", {})
        dominant_ratio = summary.get(dominant, {}).get("ratio", 0)
        recs.append(
            f"方向严重偏斜：{dominant} 占比 {dominant_ratio:.0%}，"
            "需检查 Prompt 是否存在方向偏好，或确认是否为市场趋势"
        )

    # 3. 方向胜率差异
    summary = direction.get("summary", {})
    if len(summary) >= 2:
        rates = {k: v["win_rate"] for k, v in summary.items()}
        max_dir = max(rates, key=rates.get)
        min_dir = min(rates, key=rates.get)
        if rates[max_dir] - rates[min_dir] > 0.15:
            recs.append(
                f"{min_dir} 胜率({rates[min_dir]:.0%})显著低于 "
                f"{max_dir}({rates[max_dir]:.0%})，考虑降低 {min_dir} 仓位或杠杆"
            )

    # 4. P17: 做多专项建议
    monthly_trend = direction.get("monthly_trend", {})
    if monthly_trend:
        # 连续 N 月做多亏损
        consecutive_long_loss = 0
        for _month, dirs in sorted(monthly_trend.items()):
            long_stats = dirs.get("long", {})
            if long_stats.get("count", 0) > 0 and long_stats.get("total_pnl_usdt", 0) < 0:
                consecutive_long_loss += 1
            else:
                consecutive_long_loss = 0
        if consecutive_long_loss >= 3:
            recs.append(
                f"做多连续 {consecutive_long_loss} 个月亏损，"
                "建议提高做多置信度阈值至 75+ 或暂停做多"
            )

    conf_dir = direction.get("confidence_direction", {})
    for bucket_label, dirs in conf_dir.items():
        long_stats = dirs.get("long", {})
        if long_stats.get("count", 0) >= 3 and long_stats.get("win_rate", 1) < 0.4:
            recs.append(
                f"置信度 {bucket_label} 区间做多胜率仅 {long_stats['win_rate']:.0%}"
                f" (n={long_stats['count']})，建议过滤该区间做多信号"
            )

    lev_dir = direction.get("leverage_direction", {})
    for lev_label, dirs in lev_dir.items():
        long_stats = dirs.get("long", {})
        if long_stats.get("count", 0) >= 3 and long_stats.get("total_pnl_usdt", 0) < 0:
            recs.append(
                f"{lev_label} 杠杆做多累计亏损 {long_stats['total_pnl_usdt']:+.0f} USDT"
                f" (n={long_stats['count']})，建议限制做多杠杆"
            )

    # 5. 回撤控制建议
    no_ctrl = drawdown.get("no_control", {})
    if no_ctrl.get("max_drawdown_pct", 0) > 50:
        # 找最优 daily_limit
        best_key = ""
        best_calmar = 0
        for k, v in drawdown.items():
            if k.startswith("daily_limit") and v.get("calmar", 0) > best_calmar:
                best_calmar = v["calmar"]
                best_key = k
        if best_key:
            best = drawdown[best_key]
            recs.append(
                f"最大回撤 {no_ctrl['max_drawdown_pct']:.1f}% 过高，"
                f"启用 {best_key} 可将回撤降至 {best['max_drawdown_pct']:.1f}%，"
                f"Calmar 从 {no_ctrl.get('calmar', 0):.2f} 提升至 {best_calmar:.2f}"
            )

    dyn = drawdown.get("dynamic_leverage", {})
    if dyn and no_ctrl:
        if dyn.get("max_drawdown_pct", 0) < no_ctrl.get("max_drawdown_pct", 0) * 0.7:
            recs.append(
                "动态杠杆策略有效降低回撤，建议在实盘中启用回撤感知杠杆调整"
            )

    return recs
