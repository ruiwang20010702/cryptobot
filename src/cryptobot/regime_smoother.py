"""市场状态转换平滑 — 防止 regime 在边界反复切换

要求连续 N 个周期检测到同一新 regime 才确认切换。
持久化文件: data/output/evolution/regime_history.json
"""

import json
import logging
from datetime import datetime, timezone

from cryptobot.config import DATA_OUTPUT_DIR

logger = logging.getLogger(__name__)

_HISTORY_PATH = DATA_OUTPUT_DIR / "evolution" / "regime_history.json"


def _load_history() -> dict:
    """加载 regime 历史状态"""
    if not _HISTORY_PATH.exists():
        return {
            "current_regime": "ranging",
            "pending_transition": None,
            "last_updated": datetime.now(timezone.utc).isoformat(),
        }
    try:
        return json.loads(_HISTORY_PATH.read_text())
    except (json.JSONDecodeError, OSError) as e:
        logger.warning("regime_history.json 读取失败, 使用默认: %s", e)
        return {
            "current_regime": "ranging",
            "pending_transition": None,
            "last_updated": datetime.now(timezone.utc).isoformat(),
        }


def _save_history(history: dict) -> None:
    """原子写入 regime 历史状态"""
    _HISTORY_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = _HISTORY_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(history, ensure_ascii=False, indent=2))
    tmp.rename(_HISTORY_PATH)


def smooth_regime_transition(
    detected_regime: str,
    confirm_cycles: int = 2,
    *,
    is_volatile_upgrade: bool = False,
    is_simulation: bool = False,
) -> tuple[str, bool]:
    """平滑 regime 切换，返回 (最终regime, 是否发生了切换)

    Args:
        detected_regime: 本次检测到的 regime
        confirm_cycles: 需要连续多少个周期确认才切换
        is_volatile_upgrade: 恐惧贪婪极端值触发的 volatile 升级 (跳过平滑)
        is_simulation: 模拟/回测模式 (跳过平滑)
    """
    # 紧急安全机制和模拟模式跳过平滑
    if is_volatile_upgrade or is_simulation:
        return detected_regime, False

    history = _load_history()
    current = history["current_regime"]
    changed = False

    if detected_regime == current:
        # 维持当前状态，清空 pending
        history["pending_transition"] = None
    else:
        # 不同 regime，累计 pending
        pending = history.get("pending_transition")
        if pending and pending["to"] == detected_regime:
            pending["count"] += 1
        else:
            pending = {"to": detected_regime, "count": 1}
            history["pending_transition"] = pending

        if pending["count"] >= confirm_cycles:
            # 确认切换
            history["current_regime"] = detected_regime
            history["pending_transition"] = None
            changed = True

    history["last_updated"] = datetime.now(timezone.utc).isoformat()
    _save_history(history)

    return history["current_regime"], changed
