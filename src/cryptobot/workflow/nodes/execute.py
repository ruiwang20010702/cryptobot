"""Node: execute — 写入信号文件"""

import logging

from rich.console import Console

from cryptobot.workflow.state import WorkflowState
from cryptobot.workflow.utils import _stage

logger = logging.getLogger(__name__)
_console = Console()


def execute(state: WorkflowState) -> dict:
    """根据 realtime.enabled 配置写入 signal.json 或 pending_signals.json"""
    from cryptobot.config import load_settings
    from cryptobot.signal.bridge import write_signal, write_pending_signal

    settings = load_settings()
    realtime_enabled = settings.get("realtime", {}).get("enabled", False)

    approved = state.get("approved_signals", [])
    target = "pending_signals.json" if realtime_enabled else "signal.json"
    _stage(7, f"写入信号 — {len(approved)} 个 → {target}")
    errors = list(state.get("errors", []))
    executed = []

    writer = write_pending_signal if realtime_enabled else write_signal

    from cryptobot.notify import notify_new_signal, notify_workflow_error
    from cryptobot.journal.models import SignalRecord
    from cryptobot.journal.storage import save_record
    from cryptobot.workflow.prompts import PROMPT_VERSION

    for signal in approved:
        signal["prompt_version"] = PROMPT_VERSION
        try:
            result = writer(signal)
            executed.append(result)
            logger.info("信号写入成功: %s %s → %s", signal["symbol"], signal["action"], target)
            notify_new_signal(result)
            # 记录到交易日志
            try:
                save_record(SignalRecord.from_signal(result))
            except Exception as je:
                logger.warning("交易日志记录失败: %s", je)
        except Exception as e:
            logger.error("信号写入失败 %s: %s", signal["symbol"], e)
            errors.append(f"execute_{signal['symbol']}: {e}")

    if len(errors) >= 3:
        notify_workflow_error(len(errors), errors)

    return {"executed": executed, "errors": errors}
