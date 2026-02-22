"""Regime 感知评估: 按市场状态分组对比两个时期的绩效

用于策略规则有效性评估和绩效诊断。
"""

import math
from dataclasses import dataclass


@dataclass(frozen=True)
class RegimeEvalResult:
    """单个 regime 下的前后对比结果"""

    regime: str  # "trending" | "ranging" | "volatile"
    period_a: dict  # {win_rate, avg_pnl, sharpe, count}
    period_b: dict
    improvement_pct: float  # period_b vs period_a 改善百分比 (基于 avg_pnl)
    significant: bool  # p < 0.05
    sample_size: int


def _group_by_regime(records: list) -> dict[str, list]:
    """按 regime_name 分组"""
    groups: dict[str, list] = {}
    for r in records:
        regime = r.regime_name or "unknown"
        groups.setdefault(regime, []).append(r)
    return groups


def _calc_period_stats(records: list) -> dict:
    """计算一组记录的统计指标"""
    if not records:
        return {"win_rate": 0.0, "avg_pnl": 0.0, "sharpe": 0.0, "count": 0}

    pnl_list = [r.actual_pnl_pct for r in records if r.actual_pnl_pct is not None]
    if not pnl_list:
        return {"win_rate": 0.0, "avg_pnl": 0.0, "sharpe": 0.0, "count": 0}

    wins = [p for p in pnl_list if p > 0]
    win_rate = len(wins) / len(pnl_list)
    avg_pnl = sum(pnl_list) / len(pnl_list)
    sharpe = _calc_sharpe_simple(pnl_list)

    return {
        "win_rate": round(win_rate, 4),
        "avg_pnl": round(avg_pnl, 4),
        "sharpe": round(sharpe, 4),
        "count": len(pnl_list),
    }


def _calc_sharpe_simple(returns: list[float]) -> float:
    """简单 Sharpe: mean / std"""
    if len(returns) < 2:
        return 0.0
    n = len(returns)
    mean = sum(returns) / n
    var = sum((x - mean) ** 2 for x in returns) / (n - 1)
    std = math.sqrt(var)
    if std < 1e-10:
        return 0.0
    return mean / std


def evaluate_by_regime(
    records_a: list, records_b: list
) -> list[RegimeEvalResult]:
    """按 regime 分组对比两个时期的绩效

    Args:
        records_a: Period A 记录 (较早期，基准)
        records_b: Period B 记录 (较新期，待评估)

    Returns:
        每个 regime 一个 RegimeEvalResult
    """
    from cryptobot.backtest.stats import _welch_t_test

    groups_a = _group_by_regime(records_a)
    groups_b = _group_by_regime(records_b)

    all_regimes = sorted(set(groups_a.keys()) | set(groups_b.keys()))
    results = []

    for regime in all_regimes:
        recs_a = groups_a.get(regime, [])
        recs_b = groups_b.get(regime, [])

        stats_a = _calc_period_stats(recs_a)
        stats_b = _calc_period_stats(recs_b)

        # 改善百分比 (基于 avg_pnl)
        if stats_a["avg_pnl"] != 0:
            improvement = (
                (stats_b["avg_pnl"] - stats_a["avg_pnl"])
                / abs(stats_a["avg_pnl"])
                * 100
            )
        elif stats_b["avg_pnl"] != 0:
            improvement = 100.0 if stats_b["avg_pnl"] > 0 else -100.0
        else:
            improvement = 0.0

        # 统计显著性检验
        pnl_a = [r.actual_pnl_pct for r in recs_a if r.actual_pnl_pct is not None]
        pnl_b = [r.actual_pnl_pct for r in recs_b if r.actual_pnl_pct is not None]
        p_value = _welch_t_test(pnl_a, pnl_b)
        significant = p_value < 0.05

        results.append(RegimeEvalResult(
            regime=regime,
            period_a=stats_a,
            period_b=stats_b,
            improvement_pct=round(improvement, 2),
            significant=significant,
            sample_size=stats_a["count"] + stats_b["count"],
        ))

    return results


def evaluate_rule_effectiveness(
    rule_name: str,
    before_records: list,
    after_records: list,
) -> dict:
    """评估单个规则在各 regime 下的有效性

    Args:
        rule_name: 规则名称/ID
        before_records: 规则启用前的记录
        after_records: 规则启用后的记录

    Returns:
        {rule_name, overall_verdict, by_regime: {regime: {verdict, improvement_pct, ...}}}
    """
    regime_results = evaluate_by_regime(before_records, after_records)

    by_regime = {}
    effective_count = 0
    harmful_count = 0

    for r in regime_results:
        if r.sample_size < 4:
            verdict = "insufficient_data"
        elif r.significant and r.improvement_pct > 5:
            verdict = "effective"
            effective_count += 1
        elif r.significant and r.improvement_pct < -5:
            verdict = "harmful"
            harmful_count += 1
        else:
            verdict = "neutral"

        by_regime[r.regime] = {
            "verdict": verdict,
            "improvement_pct": r.improvement_pct,
            "significant": r.significant,
            "period_a": r.period_a,
            "period_b": r.period_b,
            "sample_size": r.sample_size,
        }

    # 总体判定: 有任何 regime 显著改善则 effective
    if effective_count > 0 and harmful_count == 0:
        overall = "effective"
    elif harmful_count > 0 and effective_count == 0:
        overall = "harmful"
    elif effective_count > 0 and harmful_count > 0:
        overall = "mixed"
    else:
        overall = "neutral"

    return {
        "rule_name": rule_name,
        "overall_verdict": overall,
        "effective_regimes": effective_count,
        "harmful_regimes": harmful_count,
        "by_regime": by_regime,
    }
