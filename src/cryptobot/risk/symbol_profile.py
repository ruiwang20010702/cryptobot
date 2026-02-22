"""按币种差异化策略

基于历史交易表现对币种分 A/B/C/D 四档，差异化调整杠杆/置信度/过滤。

A: 优秀 (胜率>55% AND avg_pnl>2%)  → 正常
B: 良好 (胜率>45% AND avg_pnl>0%)  → confidence +5
C: 一般 (胜率>35% OR avg_pnl>-1%)  → leverage-1, confidence +10
D: 差   (其他)                      → blocked

持久化: data/output/evolution/symbol_profiles.json
"""

import json
import logging
from dataclasses import dataclass, asdict
from datetime import datetime, timedelta, timezone

from cryptobot.config import DATA_OUTPUT_DIR

logger = logging.getLogger(__name__)

_PROFILES_PATH = DATA_OUTPUT_DIR / "evolution" / "symbol_profiles.json"


@dataclass(frozen=True)
class SymbolGrade:
    symbol: str
    grade: str              # "A" | "B" | "C" | "D"
    win_rate: float
    avg_pnl_pct: float
    trade_count: int
    recommended_leverage: int
    min_confidence: int     # 置信度偏移量 (加到基准上)
    blocked: bool           # D 级可能禁止交易


@dataclass(frozen=True)
class SymbolProfileResult:
    grades: list[SymbolGrade]
    updated_at: str


def _calc_grade(win_rate: float, avg_pnl: float) -> str:
    """根据胜率和平均盈亏确定等级"""
    if win_rate > 0.55 and avg_pnl > 2.0:
        return "A"
    if win_rate > 0.45 and avg_pnl > 0.0:
        return "B"
    if win_rate > 0.35 or avg_pnl > -1.0:
        return "C"
    return "D"


def _grade_params(grade: str, default_leverage: int = 3) -> tuple[int, int, bool]:
    """返回 (recommended_leverage, min_confidence_offset, blocked)"""
    if grade == "A":
        return default_leverage, 0, False
    if grade == "B":
        return default_leverage, 5, False
    if grade == "C":
        return max(1, default_leverage - 1), 10, False
    # D
    return 1, 0, True


def grade_symbols(
    min_trades: int = 15,
    days: int = 180,
) -> SymbolProfileResult:
    """按币种分级

    Args:
        min_trades: 最少交易笔数，不足则标记 C 级
        days: 回溯天数
    """
    from cryptobot.journal.storage import get_all_records
    from cryptobot.config import get_all_symbols, get_pair_config

    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    all_records = get_all_records()

    # 按币种分组 closed 记录
    by_symbol: dict[str, list] = {}
    for r in all_records:
        if r.status != "closed" or r.actual_pnl_pct is None:
            continue
        if r.timestamp < cutoff:
            continue
        by_symbol.setdefault(r.symbol, []).append(r)

    all_symbols = get_all_symbols()
    grades: list[SymbolGrade] = []

    for symbol in all_symbols:
        records = by_symbol.get(symbol, [])
        count = len(records)

        if count < min_trades:
            # 数据不足，给 C 级（保守）
            pair_cfg = get_pair_config(symbol) or {}
            default_lev = pair_cfg.get("default_leverage", 3)
            lev, conf_offset, blocked = _grade_params("C", default_lev)
            grades.append(SymbolGrade(
                symbol=symbol,
                grade="C",
                win_rate=0.0,
                avg_pnl_pct=0.0,
                trade_count=count,
                recommended_leverage=lev,
                min_confidence=conf_offset,
                blocked=False,  # 数据不足不 block
            ))
            continue

        wins = [r for r in records if r.actual_pnl_pct > 0]
        win_rate = len(wins) / count
        avg_pnl = sum(r.actual_pnl_pct for r in records) / count

        grade = _calc_grade(win_rate, avg_pnl)
        pair_cfg = get_pair_config(symbol) or {}
        default_lev = pair_cfg.get("default_leverage", 3)
        lev, conf_offset, blocked = _grade_params(grade, default_lev)

        grades.append(SymbolGrade(
            symbol=symbol,
            grade=grade,
            win_rate=round(win_rate, 4),
            avg_pnl_pct=round(avg_pnl, 4),
            trade_count=count,
            recommended_leverage=lev,
            min_confidence=conf_offset,
            blocked=blocked,
        ))

    result = SymbolProfileResult(
        grades=grades,
        updated_at=datetime.now(timezone.utc).isoformat(),
    )

    _save_profiles(result)
    return result


def load_symbol_profiles() -> dict[str, SymbolGrade]:
    """加载缓存的币种分级"""
    if not _PROFILES_PATH.exists():
        return {}

    try:
        data = json.loads(_PROFILES_PATH.read_text())
    except (json.JSONDecodeError, OSError):
        return {}

    grades = data.get("grades", [])
    return {
        g["symbol"]: SymbolGrade(**g)
        for g in grades
    }


def get_symbol_grade(symbol: str) -> SymbolGrade | None:
    """获取单个币种的分级"""
    profiles = load_symbol_profiles()
    return profiles.get(symbol)


def _save_profiles(result: SymbolProfileResult) -> None:
    """保存分级结果"""
    _PROFILES_PATH.parent.mkdir(parents=True, exist_ok=True)

    data = {
        "grades": [asdict(g) for g in result.grades],
        "updated_at": result.updated_at,
    }

    tmp = _PROFILES_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2))
    tmp.rename(_PROFILES_PATH)
    logger.info("币种分级已保存: %d 个币种", len(result.grades))
