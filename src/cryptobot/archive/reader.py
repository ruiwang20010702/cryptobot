"""归档读取器 — 列表/查询/历史"""

import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

ARCHIVE_BASE = Path("data/output/archive")

# 按月份缓存文件列表索引: {month_str: [file_paths]}
_month_index_cache: dict[str, list[Path]] = {}
_month_index_mtime: dict[str, float] = {}


def _get_month_files(month_dir: Path) -> list[Path]:
    """获取月份目录下的文件列表（带缓存）"""
    month_key = month_dir.name
    try:
        current_mtime = month_dir.stat().st_mtime
    except OSError:
        return []

    cached_mtime = _month_index_mtime.get(month_key)
    if cached_mtime == current_mtime and month_key in _month_index_cache:
        return _month_index_cache[month_key]

    files = sorted(month_dir.glob("*.json"), reverse=True)
    _month_index_cache[month_key] = files
    _month_index_mtime[month_key] = current_mtime
    return files


def list_archives(month: str | None = None, limit: int = 20) -> list[dict]:
    """列出归档摘要，按时间倒序

    Args:
        month: 指定月份 (如 "2026-02")，None 则扫描全部
        limit: 最多返回条数
    """
    if not ARCHIVE_BASE.exists():
        return []

    files: list[Path] = []
    if month:
        month_dir = ARCHIVE_BASE / month
        if month_dir.exists():
            files = _get_month_files(month_dir)
    else:
        for month_dir in sorted(ARCHIVE_BASE.iterdir(), reverse=True):
            if month_dir.is_dir():
                files.extend(_get_month_files(month_dir))

    summaries = []
    for f in files[:limit]:
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            summaries.append({
                "run_id": data.get("run_id", f.stem),
                "timestamp": data.get("timestamp", ""),
                "regime": data.get("regime", {}).get("regime", "unknown"),
                "screened": len(data.get("screened_symbols", [])),
                "decisions": len(data.get("decisions", [])),
                "approved": len(data.get("approved_signals", [])),
                "errors": len(data.get("errors", [])),
            })
        except Exception as e:
            logger.warning("读取归档 %s 失败: %s", f, e)
    return summaries


def get_archive(run_id: str) -> dict | None:
    """读取单个归档，自动搜索月份目录"""
    if not ARCHIVE_BASE.exists():
        return None

    for month_dir in ARCHIVE_BASE.iterdir():
        if not month_dir.is_dir():
            continue
        filepath = month_dir / f"{run_id}.json"
        if filepath.exists():
            return json.loads(filepath.read_text(encoding="utf-8"))
    return None


def get_symbol_history(symbol: str, days: int = 30) -> list[dict]:
    """查询某币种的决策历史

    返回最近 N 天内包含该币种的归档摘要。
    """
    from datetime import datetime, timezone, timedelta

    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    results = []

    if not ARCHIVE_BASE.exists():
        return results

    for month_dir in sorted(ARCHIVE_BASE.iterdir(), reverse=True):
        if not month_dir.is_dir():
            continue
        for f in _get_month_files(month_dir):
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
                ts = data.get("timestamp", "")
                if ts and datetime.fromisoformat(ts) < cutoff:
                    continue

                # 检查该币种是否在筛选/决策/批准中出现
                screened = data.get("screened_symbols", [])
                decisions = data.get("decisions", [])
                approved = data.get("approved_signals", [])

                decision_for_sym = next(
                    (d for d in decisions if d.get("symbol") == symbol), None
                )
                approved_for_sym = next(
                    (s for s in approved if s.get("symbol") == symbol), None
                )

                if symbol in screened or decision_for_sym or approved_for_sym:
                    results.append({
                        "run_id": data.get("run_id", f.stem),
                        "timestamp": ts,
                        "regime": data.get("regime", {}).get("regime", "unknown"),
                        "screened": symbol in screened,
                        "decision": decision_for_sym,
                        "approved": approved_for_sym is not None,
                    })
            except Exception as e:
                logger.warning("读取归档 %s 失败: %s", f, e)

    return results
