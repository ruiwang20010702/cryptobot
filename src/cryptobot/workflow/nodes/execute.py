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
    from cryptobot.workflow.prompts import get_prompt_version
    from cryptobot.freqtrade_api import ft_api_get

    # H1: 获取现有持仓，避免重复开仓
    existing_positions = ft_api_get("/status") or []
    held_symbols = {}
    for pos in existing_positions:
        pair = pos.get("pair", "")
        sym = pair.replace("/", "").replace(":USDT", "")
        direction = "short" if pos.get("is_short") else "long"
        held_symbols[sym] = direction

    for signal in approved:
        # H1: 跳过已有同币种同方向持仓
        sym = signal.get("symbol", "")
        sig_action = signal.get("action", "")
        if sym in held_symbols and held_symbols[sym] == sig_action:
            logger.info("跳过 %s: 已持有同方向 %s 仓位", sym, sig_action)
            _console.print(f"    [yellow]跳过 {sym}: 已有 {sig_action} 持仓[/yellow]")
            continue
        signal["prompt_version"] = get_prompt_version()
        try:
            result = writer(signal)
            executed.append(result)
            logger.info("信号写入成功: %s %s → %s", signal["symbol"], signal["action"], target)
            notify_new_signal(result)
            # 记录到交易日志
            try:
                record = SignalRecord.from_signal(result)
                # 竞赛模式: 记录 model_id
                if signal.get("model_id"):
                    record.model_id = signal["model_id"]
                save_record(record)
                # 竞赛结果记录
                if signal.get("model_id"):
                    try:
                        from cryptobot.evolution.model_competition import (
                            record_competition_result,
                        )
                        record_competition_result(
                            record.symbol, signal["model_id"],
                            record.action, record.signal_id,
                        )
                    except Exception:
                        pass
            except Exception as je:
                logger.warning("交易日志记录失败: %s", je)
        except Exception as e:
            logger.error("信号写入失败 %s: %s", signal["symbol"], e)
            errors.append(f"execute_{signal['symbol']}: {e}")

    if len(errors) >= 3:
        notify_workflow_error(len(errors), errors)

    # 每轮分析摘要通知
    try:
        from cryptobot.notify import notify_workflow_summary
        regime = state.get("market_regime", {})
        capital_tier = state.get("capital_tier", {})
        fg = state.get("fear_greed", {})
        notify_workflow_summary(
            screened=state.get("screened_symbols", []),
            decisions=state.get("decisions", []),
            approved_count=len(executed),
            regime=regime.get("regime", "unknown"),
            capital_tier=capital_tier.get("tier", "unknown"),
            fear_greed=fg.get("current_value"),
        )
    except Exception as e:
        logger.warning("分析摘要通知失败: %s", e)

    return {"executed": executed, "errors": errors}
