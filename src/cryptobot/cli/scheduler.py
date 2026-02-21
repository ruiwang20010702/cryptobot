"""调度器: cryptobot daemon — 一个命令跑起所有定时任务

定时任务:
- 完整分析工作流 (默认每 2h)
- 告警检查 (默认每 5min)
- 持仓复审 (默认每 4h)
- 过期信号清理 (默认每 24h)
- 可选: 实时入场监控 (后台线程)
- 配置热更新检查 (每 2min)
"""

import logging
import os
import threading

import click
from rich.console import Console

from cryptobot.config import load_settings

console = Console()
logger = logging.getLogger(__name__)

# ─── 配置热更新 ──────────────────────────────────────────────────────────

_last_mtime: float = 0.0
_last_config: dict = {}
_CONFIG_PATH = os.path.join("config", "settings.yaml")


def _maybe_reload_config(scheduler) -> None:
    """检查 settings.yaml 是否变更，变更时热更新调度间隔"""
    global _last_mtime, _last_config

    try:
        mtime = os.path.getmtime(_CONFIG_PATH)
    except OSError:
        return  # 文件不存在不报错

    if mtime == _last_mtime:
        return

    _last_mtime = mtime

    try:
        import yaml
        with open(_CONFIG_PATH) as f:
            new_config = yaml.safe_load(f) or {}
    except Exception as e:
        logger.warning("配置文件解析失败: %s", e)
        return

    old_config = _last_config
    _last_config = new_config

    if not old_config:
        return  # 首次加载，不触发 reschedule

    _maybe_reschedule(scheduler, new_config, old_config)


def _maybe_reschedule(scheduler, new: dict, old: dict) -> None:
    """对比新旧配置中的 schedule 区段，变化时调用 reschedule_job"""
    new_sched = new.get("schedule", {})
    old_sched = old.get("schedule", {})

    mapping = {
        "full_cycle_hours": ("workflow_run", "hours"),
        "monitor_interval_minutes": ("check_alerts", "minutes"),
        "re_review_hours": ("re_review", "hours"),
        "cleanup_hours": ("cleanup", "hours"),
    }

    for key, (job_id, unit) in mapping.items():
        new_val = new_sched.get(key)
        old_val = old_sched.get(key)
        if new_val is not None and new_val != old_val:
            try:
                scheduler.reschedule_job(
                    job_id, trigger="interval", **{unit: new_val},
                )
                logger.info("热更新 %s: %s=%s", job_id, unit, new_val)
            except Exception as e:
                logger.warning("热更新 %s 失败: %s", job_id, e)


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


def _format_daily_report(today: dict, weekly: dict,
                         positions: list, accuracy: dict) -> str:
    """格式化每日绩效报告"""
    from datetime import date

    lines = [f"\U0001f4ca CryptoBot 日报 ({date.today().isoformat()})"]

    # 今日统计
    t_closed = today.get("closed", 0)
    if t_closed > 0:
        t_wins = round(today.get("win_rate", 0) * t_closed)
        t_losses = t_closed - t_wins
        t_pnl_pct = today.get("avg_pnl_pct", 0) * t_closed
        t_pnl_usdt = today.get("total_pnl_usdt", 0)
        lines.append("")
        lines.append(
            f"今日: {t_closed} 笔 ({t_wins}胜 {t_losses}负) "
            f"{t_pnl_pct:+.1f}% ({t_pnl_usdt:+.0f} USDT)"
        )
    else:
        lines.append("")
        lines.append("今日无交易记录")

    # 本周统计
    w_closed = weekly.get("closed", 0)
    if w_closed > 0:
        w_wr = weekly.get("win_rate", 0) * 100
        w_pf = weekly.get("profit_factor", 0)
        w_pnl_usdt = weekly.get("total_pnl_usdt", 0)
        lines.append(
            f"本周: 胜率 {w_wr:.0f}% 盈亏比 {w_pf}:1 {w_pnl_usdt:+.0f} USDT"
        )

    # 持仓列表
    if positions:
        lines.append(f"\n持仓 {len(positions)} 个:")
        for p in positions:
            pair = p.get("pair", "?")
            symbol = pair.replace("/", "").replace(":USDT", "")
            is_short = p.get("is_short", False)
            direction = "SHORT" if is_short else "LONG"
            leverage = p.get("leverage", "?")
            profit = (p.get("profit_ratio", 0) or 0) * 100
            lines.append(f"  {symbol} {direction} {leverage}x {profit:+.1f}%")
    else:
        lines.append("\n持仓: 0 个")

    # 分析师准确率
    if accuracy:
        parts = []
        for role in ("technical", "onchain", "sentiment", "fundamental"):
            info = accuracy.get(role)
            if info and info.get("total", 0) > 0:
                acc_pct = info.get("accuracy", 0) * 100
                parts.append(f"{role} {acc_pct:.0f}%")
        if parts:
            lines.append(f"\n分析师30天: {' | '.join(parts)}")

    return "\n".join(lines)


def job_daily_report() -> None:
    """定时: 每日绩效日报推送 Telegram"""
    from cryptobot.journal.analytics import calc_performance, calc_analyst_accuracy
    from cryptobot.freqtrade_api import ft_api_get
    from cryptobot.notify import send_message

    try:
        today = calc_performance(days=1)
        weekly = calc_performance(days=7)
        positions = ft_api_get("/status") or []
        accuracy = calc_analyst_accuracy(days=30)

        text = _format_daily_report(today, weekly, positions, accuracy)
        if text:
            send_message(text)
        logger.info("[调度] 日报已推送")
    except Exception as e:
        logger.error("[调度] 日报推送失败: %s", e, exc_info=True)


def job_prompt_optimization() -> None:
    """定时: 自动 Prompt 优化检查"""
    from cryptobot.evolution.prompt_optimizer import run_optimization_cycle

    try:
        result = run_optimization_cycle()
        if result["triggered"]:
            logger.info("[调度] Prompt 优化触发: %s", result["reason"])
            from cryptobot.notify import send_message
            send_message(
                f"Prompt 自动优化: {result['new_version']}\n{result['reason']}"
            )
        else:
            logger.debug("[调度] Prompt 优化检查: %s", result["reason"])
    except Exception as e:
        logger.error("[调度] Prompt 优化失败: %s", e, exc_info=True)


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

    # 每日绩效日报: UTC 0:05
    scheduler.add_job(
        job_daily_report,
        "cron",
        hour=0, minute=5,
        id="daily_report",
        name="每日绩效日报",
        max_instances=1,
    )

    # 每日 Prompt 自动优化: UTC 8:00
    scheduler.add_job(
        job_prompt_optimization,
        "cron",
        hour=8, minute=0,
        id="prompt_optimization",
        name="Prompt 自动优化 (每日8:00)",
        max_instances=1,
    )

    # 配置热更新检查: 每 2min
    scheduler.add_job(
        _maybe_reload_config,
        "interval",
        minutes=2,
        args=[scheduler],
        id="config_reload",
        name="配置热更新 (每2min)",
        max_instances=1,
    )

    # 初始化配置快照
    global _last_mtime, _last_config
    try:
        _last_mtime = os.path.getmtime(_CONFIG_PATH)
        import yaml
        with open(_CONFIG_PATH) as f:
            _last_config = yaml.safe_load(f) or {}
    except OSError:
        pass

    console.print("[cyan]调度器启动[/cyan]")
    console.print(f"  完整分析: 每 {full_cycle_hours}h")
    console.print(f"  告警检查: 每 {monitor_interval_min}min")
    console.print(f"  持仓复审: 每 {re_review_hours}h")
    console.print(f"  信号清理: 每 {cleanup_hours}h")

    # 用于优雅停止监控线程
    stop_event = threading.Event()
    evt_enabled = settings.get("events", {}).get("enabled", False)

    # WebSocket 价格推送 (后台线程，优先于 realtime/events)
    ws_enabled = settings.get("websocket", {}).get("enabled", True)
    if ws_enabled and (rt_enabled or evt_enabled):
        from cryptobot.config import get_all_symbols
        from cryptobot.realtime.ws_price_feed import run_ws_price_feed

        ws_symbols = get_all_symbols()
        ws_thread = threading.Thread(
            target=run_ws_price_feed,
            args=[ws_symbols],
            kwargs={"stop_event": stop_event},
            daemon=True,
            name="ws-price-feed",
        )
        ws_thread.start()
        console.print(f"  WS 价格推送: 已启用 ({len(ws_symbols)} 币种)")
    else:
        console.print("  WS 价格推送: 未启用")

    # 实时入场监控 (后台线程)
    if rt_enabled:
        from cryptobot.realtime.monitor import run_monitor

        rt_thread = threading.Thread(
            target=run_monitor, kwargs={"stop_event": stop_event},
            daemon=True, name="realtime-monitor",
        )
        rt_thread.start()
        console.print("  实时监控: 已启用 (后台线程)")
    else:
        console.print("  实时监控: 未启用")

    # 价格异动事件监控 (后台线程)
    if evt_enabled:
        from cryptobot.events.price_monitor import run_price_monitor

        evt_thread = threading.Thread(
            target=run_price_monitor, kwargs={"stop_event": stop_event},
            daemon=True, name="event-monitor",
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
        stop_event.set()
        console.print("\n[yellow]调度器已停止[/yellow]")
