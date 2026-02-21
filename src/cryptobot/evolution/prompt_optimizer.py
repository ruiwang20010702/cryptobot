"""绩效驱动的自动 Prompt 迭代

定期检查绩效，自动分析失败模式并生成改进 prompt。
持久化: data/output/evolution/iterations.json
"""

import json
import logging
from datetime import datetime, timezone

from cryptobot.config import DATA_OUTPUT_DIR

logger = logging.getLogger(__name__)

_ITERATIONS_DIR = DATA_OUTPUT_DIR / "evolution"
_ITERATIONS_FILE = _ITERATIONS_DIR / "iterations.json"


def _load_iterations() -> list:
    if not _ITERATIONS_FILE.exists():
        return []
    try:
        data = json.loads(_ITERATIONS_FILE.read_text())
        return data if isinstance(data, list) else []
    except (json.JSONDecodeError, OSError):
        return []


def _save_iterations(iterations: list) -> None:
    _ITERATIONS_DIR.mkdir(parents=True, exist_ok=True)
    tmp = _ITERATIONS_FILE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(iterations, ensure_ascii=False, indent=2))
    tmp.rename(_ITERATIONS_FILE)


def check_performance_decline(days_short: int = 7, days_long: int = 30) -> dict:
    """比较短期 vs 长期胜率，检测绩效退化

    Returns:
        {"declined": bool, "win_rate_7d", "win_rate_30d", "gap_pct",
         "closed_7d", "closed_30d"}
    """
    from cryptobot.journal.analytics import calc_performance

    short = calc_performance(days_short)
    long = calc_performance(days_long)

    wr_short = short.get("win_rate", 0)
    wr_long = long.get("win_rate", 0)
    closed_short = short.get("closed", 0)
    closed_long = long.get("closed", 0)

    gap_pct = 0.0
    if wr_long > 0:
        gap_pct = round((wr_long - wr_short) / wr_long * 100, 1)

    declined = (
        closed_short >= 10
        and wr_long > 0
        and wr_short < wr_long * 0.8
    )

    return {
        "declined": declined,
        "win_rate_7d": wr_short,
        "win_rate_30d": wr_long,
        "gap_pct": gap_pct,
        "closed_7d": closed_short,
        "closed_30d": closed_long,
    }


def analyze_failures(days: int = 7) -> str:
    """提取近期亏损交易的共性模式

    Returns:
        失败分析摘要文本
    """
    from datetime import timedelta
    from cryptobot.journal.storage import get_all_records

    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    records = get_all_records()

    losses = [
        r for r in records
        if r.status == "closed"
        and r.timestamp >= cutoff
        and (r.actual_pnl_pct or 0) < 0
    ]

    if not losses:
        return "近期无亏损交易记录。"

    lines = [f"近 {days} 天 {len(losses)} 笔亏损交易分析:"]
    for r in losses:
        votes = r.analyst_votes or {}
        vote_str = ", ".join(f"{k}={v}" for k, v in votes.items())
        lines.append(
            f"- {r.symbol} {r.action} conf={r.confidence} "
            f"pnl={r.actual_pnl_pct:+.1f}% "
            f"reason={r.reasoning[:80]}... "
            f"votes=[{vote_str}]"
        )

    # 统计共性模式
    directions = {"long": 0, "short": 0}
    low_conf = 0
    analyst_disagree = 0
    for r in losses:
        directions[r.action] = directions.get(r.action, 0) + 1
        if r.confidence and r.confidence < 65:
            low_conf += 1
        votes = r.analyst_votes or {}
        vote_dirs = set(votes.values())
        if len(vote_dirs) > 1:
            analyst_disagree += 1

    lines.append("")
    lines.append("共性模式:")
    lines.append(f"  方向分布: long={directions.get('long',0)} short={directions.get('short',0)}")
    if low_conf > 0:
        lines.append(f"  低置信度入场 (<65): {low_conf}/{len(losses)} 笔")
    if analyst_disagree > 0:
        lines.append(f"  分析师意见分歧: {analyst_disagree}/{len(losses)} 笔")

    return "\n".join(lines)


def analyze_wins(days: int = 7) -> str:
    """分析过早退出和仓位不足的盈利交易"""
    from datetime import timedelta
    from cryptobot.journal.storage import get_all_records

    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    records = get_all_records()

    wins = [
        r for r in records
        if r.status == "closed"
        and r.timestamp >= cutoff
        and (r.actual_pnl_pct or 0) > 0
    ]

    if not wins:
        return "近期无盈利交易记录。"

    lines = [f"近 {days} 天 {len(wins)} 笔盈利交易分析:"]
    early_exits = 0
    small_positions = 0
    for r in wins:
        mfe = getattr(r, "mfe_pct", None)
        if mfe and r.actual_pnl_pct and mfe > r.actual_pnl_pct * 2:
            early_exits += 1
            lines.append(
                f"- {r.symbol} 过早退出: 实际盈利 {r.actual_pnl_pct:+.1f}% "
                f"但 MFE={mfe:.1f}% (少赚 {mfe - r.actual_pnl_pct:.1f}%)"
            )
        if r.confidence and r.confidence >= 80 and r.actual_pnl_pct and r.actual_pnl_pct > 5:
            small_positions += 1

    if early_exits:
        lines.append(f"\n过早退出: {early_exits}/{len(wins)} 笔 (MFE >> 实际盈利)")
    if small_positions:
        lines.append(f"高信心大盈利但仓位可能不足: {small_positions} 笔")

    return "\n".join(lines)


def generate_improved_prompt(failure_analysis: str) -> dict:
    """用 LLM 基于失败分析生成改进 prompt addon

    Returns:
        {"addons": {"TRADER": "...", "RISK_MANAGER": "..."}, "note": "..."}
    """
    from cryptobot.workflow.llm import call_claude

    prompt = f"""基于以下失败交易分析，生成改进建议作为 prompt addon 段落。

{failure_analysis}

请生成 JSON，包含:
- addons: 字典，key 为角色 (TRADER / RISK_MANAGER)，value 为改进段落 (中文)
- note: 一句话描述改进要点

只输出 JSON，不要其他内容。"""

    schema = {
        "type": "object",
        "properties": {
            "addons": {
                "type": "object",
                "properties": {
                    "TRADER": {"type": "string"},
                    "RISK_MANAGER": {"type": "string"},
                },
            },
            "note": {"type": "string"},
        },
        "required": ["addons", "note"],
    }

    result = call_claude(prompt, model="sonnet", role="trader", json_schema=schema)

    if isinstance(result, dict) and "addons" in result:
        return result
    return {"addons": {}, "note": "AI 生成失败"}


def run_optimization_cycle() -> dict:
    """运行一次完整优化周期

    Returns:
        {"triggered": bool, "new_version": str|None, "reason": str}
    """
    from cryptobot.evolution.prompt_manager import create_version, activate_version
    from cryptobot.workflow.prompts import reset_prompt_version_cache

    # 1. 检查绩效下降
    decline = check_performance_decline()
    if not decline["declined"]:
        return {
            "triggered": False,
            "new_version": None,
            "reason": f"绩效未退化 (7d胜率={decline['win_rate_7d']:.1%}, "
                      f"30d胜率={decline['win_rate_30d']:.1%})",
        }

    logger.info(
        "检测到绩效退化: 7d=%.1f%% vs 30d=%.1f%% (差距 %.1f%%)",
        decline["win_rate_7d"] * 100,
        decline["win_rate_30d"] * 100,
        decline["gap_pct"],
    )

    # 2. 分析失败模式
    analysis = analyze_failures(7)
    logger.info("失败分析完成: %d 字", len(analysis))

    # 2b. 分析盈利模式
    win_analysis = analyze_wins(7)
    logger.info("盈利分析完成: %d 字", len(win_analysis))

    # 3. AI 生成改进
    improvement = generate_improved_prompt(analysis + "\n\n" + win_analysis)

    # 4. 创建新版本
    addons = improvement.get("addons", {})
    note = improvement.get("note", "自动优化")
    new_version = create_version(note=f"[自动优化] {note}", addons=addons)

    # 5. 激活新版本
    activate_version(new_version)
    reset_prompt_version_cache()

    # 6. 记录迭代
    iteration = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "decline": decline,
        "failure_analysis": analysis[:500],
        "new_version": new_version,
        "note": note,
    }
    iterations = _load_iterations()
    iterations.append(iteration)
    _save_iterations(iterations)

    logger.info("自动优化完成: 新版本 %s — %s", new_version, note)

    return {
        "triggered": True,
        "new_version": new_version,
        "reason": f"绩效退化 ({decline['gap_pct']:.1f}%), AI 生成改进: {note}",
    }
