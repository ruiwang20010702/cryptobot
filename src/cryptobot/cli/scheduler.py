"""调度器: cryptobot daemon — 一个命令跑起所有定时任务

定时任务:
- 完整分析工作流 (默认每 2h)
- 告警检查 (默认每 5min)
- 持仓复审 (默认每 4h)
- 过期信号清理 (默认每 24h)
- 可选: 实时入场监控 (后台线程)
"""

import logging
import threading

import click
from rich.console import Console

from cryptobot.config import load_settings

console = Console()
logger = logging.getLogger(__name__)


# ─── 定时任务函数 ──────────────────────────────────────────────────────────

def job_workflow_run() -> None:
    """定时: 完整分析工作流"""
    from cryptobot.workflow.graph import build_graph

    logger.info("[调度] 开始完整分析工作流...")
    try:
        app = build_graph()
        final_state = app.invoke({})
        executed = final_state.get("executed", [])
        errors = final_state.get("errors", [])
        logger.info(
            "[调度] 工作流完成: %d 信号写入, %d 错误",
            len(executed), len(errors),
        )
    except Exception as e:
        logger.error("[调度] 工作流失败: %s", e, exc_info=True)


def job_check_alerts() -> None:
    """定时: 检查告警"""
    from cryptobot.cli.monitor import _build_position_alerts, _build_signal_only_alerts
    from cryptobot.freqtrade_api import ft_api_get
    from cryptobot.signal.bridge import read_signals

    try:
        signals = read_signals(filter_expired=False)
        positions = ft_api_get("/status")

        if positions:
            alerts = _build_position_alerts(positions, signals)
        else:
            alerts = _build_signal_only_alerts(signals)

        critical = [a for a in alerts if a["level"] == "CRITICAL"]
        warning = [a for a in alerts if a["level"] == "WARNING"]

        if critical or warning:
            logger.warning(
                "[调度] 告警: %d CRITICAL, %d WARNING",
                len(critical), len(warning),
            )
            from cryptobot.notify import notify_alert
            for a in critical:
                logger.warning("  CRITICAL: %s", a["message"])
                notify_alert("CRITICAL", a["message"])
            for a in warning:
                logger.warning("  WARNING: %s", a["message"])
                notify_alert("WARNING", a["message"])
        else:
            logger.debug("[调度] 告警检查: 一切正常")
    except Exception as e:
        logger.error("[调度] 告警检查失败: %s", e, exc_info=True)


def job_re_review() -> None:
    """定时: 持仓复审"""
    from cryptobot.freqtrade_api import ft_api_get
    from cryptobot.signal.bridge import update_signal_field

    try:
        positions = ft_api_get("/status")
        if not positions:
            logger.debug("[调度] 无持仓，跳过复审")
            return

        from cryptobot.workflow.graph import collect_data_for_symbols, re_review

        held_symbols = [
            p["pair"].replace("/", "").replace(":USDT", "")
            for p in positions
        ]
        state = collect_data_for_symbols(held_symbols)
        suggestions = re_review(positions, state)

        for s in suggestions:
            if s["decision"] == "adjust_stop_loss" and s.get("new_stop_loss"):
                updated = update_signal_field(
                    s["symbol"], "stop_loss", s["new_stop_loss"],
                )
                if updated:
                    logger.info(
                        "[调度] 更新 %s 止损 → %s", s["symbol"], s["new_stop_loss"],
                    )

        logger.info("[调度] 复审完成: %d 个持仓", len(positions))
    except Exception as e:
        logger.error("[调度] 复审失败: %s", e, exc_info=True)


def job_cleanup() -> None:
    """定时: 清理过期信号"""
    from cryptobot.signal.bridge import cleanup_expired

    try:
        removed = cleanup_expired()
        if removed:
            logger.info("[调度] 清理过期信号: %d 个", removed)
    except Exception as e:
        logger.error("[调度] 清理失败: %s", e, exc_info=True)


def job_journal_sync() -> None:
    """定时: 同步 Freqtrade 平仓数据到交易日志"""
    from cryptobot.journal.storage import get_records_by_status, update_record
    from cryptobot.freqtrade_api import ft_api_get

    try:
        trades = ft_api_get("/trades") or []
        closed_trades = [t for t in trades if t.get("is_open") is False]
        active_records = get_records_by_status("active")

        synced = 0
        for record in active_records:
            ft_pair = record.symbol[:3] + "/" + record.symbol[3:] + ":USDT"
            for trade in closed_trades:
                if trade.get("pair") != ft_pair:
                    continue
                if trade.get("is_short", False) != (record.action == "short"):
                    continue

                pnl_pct = (trade.get("profit_ratio", 0) or 0) * 100
                pnl_usdt = trade.get("profit_abs", 0) or 0

                update_record(
                    record.signal_id,
                    status="closed",
                    actual_entry_price=trade.get("open_rate"),
                    actual_exit_price=trade.get("close_rate"),
                    actual_pnl_pct=round(pnl_pct, 2),
                    actual_pnl_usdt=round(pnl_usdt, 2),
                )
                synced += 1
                break

        if synced:
            logger.info("[调度] 交易日志同步: %d 笔", synced)
    except Exception as e:
        logger.error("[调度] 交易日志同步失败: %s", e, exc_info=True)


# ─── CLI ───────────────────────────────────────────────────────────────────

@click.group()
def daemon():
    """后台调度服务"""
    pass


@daemon.command("start")
@click.option("--run-now", is_flag=True, help="启动时立即运行一次完整分析")
@click.option("--verbose", "-v", is_flag=True, help="详细日志")
def start(run_now: bool, verbose: bool):
    """启动调度器 (2h 分析 + 5min 告警 + 4h 复审 + 24h 清理)"""
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )

    settings = load_settings()
    schedule_cfg = settings.get("schedule", {})
    full_cycle_hours = schedule_cfg.get("full_cycle_hours", 2)
    monitor_interval_min = schedule_cfg.get("monitor_interval_minutes", 5)
    re_review_hours = schedule_cfg.get("re_review_hours", 4)
    cleanup_hours = schedule_cfg.get("cleanup_hours", 24)
    rt_enabled = settings.get("realtime", {}).get("enabled", False)

    from apscheduler.schedulers.blocking import BlockingScheduler

    scheduler = BlockingScheduler()

    # 完整分析工作流
    scheduler.add_job(
        job_workflow_run,
        "interval",
        hours=full_cycle_hours,
        id="workflow_run",
        name=f"完整分析 (每{full_cycle_hours}h)",
        max_instances=1,
        misfire_grace_time=300,
    )

    # 告警检查
    scheduler.add_job(
        job_check_alerts,
        "interval",
        minutes=monitor_interval_min,
        id="check_alerts",
        name=f"告警检查 (每{monitor_interval_min}min)",
        max_instances=1,
    )

    # 持仓复审
    scheduler.add_job(
        job_re_review,
        "interval",
        hours=re_review_hours,
        id="re_review",
        name=f"持仓复审 (每{re_review_hours}h)",
        max_instances=1,
        misfire_grace_time=300,
    )

    # 过期信号清理
    scheduler.add_job(
        job_cleanup,
        "interval",
        hours=cleanup_hours,
        id="cleanup",
        name=f"信号清理 (每{cleanup_hours}h)",
        max_instances=1,
    )

    # 交易日志同步: 每 30min
    scheduler.add_job(
        job_journal_sync,
        "interval",
        minutes=30,
        id="journal_sync",
        name="交易日志同步 (每30min)",
        max_instances=1,
    )

    console.print("[cyan]调度器启动[/cyan]")
    console.print(f"  完整分析: 每 {full_cycle_hours}h")
    console.print(f"  告警检查: 每 {monitor_interval_min}min")
    console.print(f"  持仓复审: 每 {re_review_hours}h")
    console.print(f"  信号清理: 每 {cleanup_hours}h")

    # 实时入场监控 (后台线程)
    if rt_enabled:
        from cryptobot.realtime.monitor import run_monitor

        rt_thread = threading.Thread(
            target=run_monitor, daemon=True, name="realtime-monitor",
        )
        rt_thread.start()
        console.print("  实时监控: 已启用 (后台线程)")
    else:
        console.print("  实时监控: 未启用")

    # 价格异动事件监控 (后台线程)
    evt_enabled = settings.get("events", {}).get("enabled", False)
    if evt_enabled:
        from cryptobot.events.price_monitor import run_price_monitor

        evt_thread = threading.Thread(
            target=run_price_monitor, daemon=True, name="event-monitor",
        )
        evt_thread.start()
        console.print("  事件监控: 已启用 (后台线程)")
    else:
        console.print("  事件监控: 未启用")

    console.print("\n按 Ctrl+C 停止\n")

    # 启动时立即运行一次告警检查
    job_check_alerts()

    if run_now:
        console.print("[cyan]立即运行一次完整分析...[/cyan]")
        job_workflow_run()

    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        console.print("\n[yellow]调度器已停止[/yellow]")
