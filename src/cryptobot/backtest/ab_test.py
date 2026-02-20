"""Prompt A/B 测试框架

按 prompt_version 分组对比胜率和盈亏比，评估不同版本 prompt 的效果。
需要足够已平仓 journal 记录作为 ground truth。
"""

from datetime import datetime, timezone, timedelta

from cryptobot.journal.storage import get_all_records


def run_ab_test(days: int = 90) -> dict:
    """按 prompt_version 分组对比绩效

    Args:
        days: 回溯天数

    Returns:
        {
            "versions": {
                "v1.0": {"count", "wins", "losses", "win_rate", "avg_pnl_pct", "profit_factor"},
                ...
            },
            "total_samples": int,
            "period_days": int,
        }
    """
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    all_records = get_all_records()

    closed = [
        r for r in all_records
        if r.status == "closed"
        and r.timestamp >= cutoff
        and r.actual_pnl_pct is not None
    ]

    # 按 prompt_version 分组
    groups: dict[str, list] = {}
    for r in closed:
        version = r.prompt_version or "unknown"
        if version not in groups:
            groups[version] = []
        groups[version].append(r)

    versions = {}
    for version, records in groups.items():
        wins = [r for r in records if r.actual_pnl_pct > 0]
        losses = [r for r in records if r.actual_pnl_pct <= 0]
        win_rate = len(wins) / len(records) if records else 0

        gross_profit = sum(r.actual_pnl_pct for r in wins)
        gross_loss = abs(sum(r.actual_pnl_pct for r in losses))
        if gross_loss > 0:
            profit_factor = round(gross_profit / gross_loss, 2)
        elif gross_profit > 0:
            profit_factor = float("inf")
        else:
            profit_factor = 0

        pnl_list = [r.actual_pnl_pct for r in records]
        avg_pnl = sum(pnl_list) / len(pnl_list) if pnl_list else 0

        versions[version] = {
            "count": len(records),
            "wins": len(wins),
            "losses": len(losses),
            "win_rate": round(win_rate, 3),
            "avg_pnl_pct": round(avg_pnl, 2),
            "profit_factor": profit_factor,
        }

    return {
        "versions": versions,
        "total_samples": len(closed),
        "period_days": days,
    }
