"""归档写入器 — 将工作流完整决策链保存为 JSON"""

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

ARCHIVE_BASE = Path("data/output/archive")


def _ensure_archive_dir(month_str: str) -> Path:
    """确保月份目录存在"""
    d = ARCHIVE_BASE / month_str
    d.mkdir(parents=True, exist_ok=True)
    return d


def _generate_run_id(regime: str) -> str:
    """生成 run_id: YYYYMMDD_HHMM_regime"""
    now = datetime.now(timezone.utc)
    ts = now.strftime("%Y%m%d_%H%M")
    regime_name = regime or "unknown"
    return f"{ts}_{regime_name}"


def save_archive(state: dict, extra: dict | None = None) -> str:
    """从 WorkflowState 提取所有字段，组装并写入归档文件

    Returns:
        run_id
    """
    regime_info = state.get("market_regime", {})
    regime_name = regime_info.get("regime", "unknown")
    run_id = _generate_run_id(regime_name)
    now = datetime.now(timezone.utc)

    archive = {
        "run_id": run_id,
        "timestamp": now.isoformat(),
        "regime": regime_info,
        "capital_tier": state.get("capital_tier", {}),
        "fear_greed": state.get("fear_greed", {}),
        "screened_symbols": state.get("screened_symbols", []),
        "screening_scores": state.get("screening_scores", []),
        "analyses": state.get("analyses", {}),
        "research": state.get("research", {}),
        "decisions": state.get("decisions", []),
        "risk_details": state.get("risk_details", {}),
        "approved_signals": state.get("approved_signals", []),
        "executed": state.get("executed", []),
        "errors": state.get("errors", []),
    }

    if extra:
        archive.update(extra)

    month_str = now.strftime("%Y-%m")
    archive_dir = _ensure_archive_dir(month_str)
    filepath = archive_dir / f"{run_id}.json"

    # 原子写入
    tmp = filepath.with_suffix(".json.tmp")
    try:
        tmp.write_text(json.dumps(archive, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(filepath)
        logger.info("归档已保存: %s", filepath)
    except Exception:
        tmp.unlink(missing_ok=True)
        raise

    return run_id
