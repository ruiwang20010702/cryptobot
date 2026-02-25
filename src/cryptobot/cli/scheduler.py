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

_config_lock = threading.Lock()
_last_mtime: float = 0.0
_last_config: dict = {}
_CONFIG_PATH = os.path.join("config", "settings.yaml")
_PID_FILE = os.path.join("data", "output", "daemon.pid")


def _check_pid_file() -> bool:
    """检查是否已有调度器进程运行，返回 True 表示可启动"""
    if os.path.exists(_PID_FILE):
        try:
            old_pid = int(open(_PID_FILE).read().strip())
            # 检查进程是否存活
            os.kill(old_pid, 0)
            return False  # 进程仍在运行
        except (ProcessLookupError, PermissionError):
            pass  # 旧进程已退出
        except (ValueError, OSError):
            pass
    return True


def _write_pid_file() -> None:
    """写入 PID 文件"""
    os.makedirs(os.path.dirname(_PID_FILE), exist_ok=True)
    with open(_PID_FILE, "w") as f:
        f.write(str(os.getpid()))


def _remove_pid_file() -> None:
    """删除 PID 文件"""
    try:
        os.remove(_PID_FILE)
    except OSError:
        pass


def _maybe_reload_config(scheduler) -> None:
    """检查 settings.yaml 是否变更，变更时热更新调度间隔"""
    global _last_mtime, _last_config

    try:
        mtime = os.path.getmtime(_CONFIG_PATH)
    except OSError:
        return  # 文件不存在不报错

    with _config_lock:
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
        "full_cycle_minutes": ("workflow_run", "minutes"),
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


def _symbol_to_ft_pair(symbol: str) -> str:
    """Binance 格式 BTCUSDT → Freqtrade 格式 BTC/USDT:USDT"""
    return symbol.removesuffix("USDT") + "/USDT:USDT"


# ─── 定时任务函数 ──────────────────────────────────────────────────────────

def _notify_task_failure(task_name: str, error: Exception) -> None:
    """任务失败时发送 Telegram 通知"""
    try:
        from cryptobot.notify import send_message
        send_message(f"[调度] 任务失败: {task_name}\n{error}")
    except Exception:
        logger.warning("[调度] 失败通知发送失败")


def _run_with_retry(task_name: str, fn, max_retries: int = 1) -> None:
    """运行任务，失败时重试一次并发送通知"""
    for attempt in range(1 + max_retries):
        try:
            fn()
            return
        except Exception as e:
            if attempt < max_retries:
                logger.warning("[调度] %s 失败 (重试 %d/%d): %s",
                               task_name, attempt + 1, max_retries, e)
            else:
                logger.error("[调度] %s 最终失败: %s", task_name, e, exc_info=True)
                _notify_task_failure(task_name, e)


def job_workflow_run() -> None:
    """定时: 完整分析工作流"""
    def _run():
        from cryptobot.workflow.graph import build_graph
        logger.info("[调度] 开始完整分析工作流...")
        app = build_graph()
        final_state = app.invoke({})
        executed = final_state.get("executed", [])
        errors = final_state.get("errors", [])
        logger.info(
            "[调度] 工作流完成: %d 信号写入, %d 错误",
            len(executed), len(errors),
        )

    _run_with_retry("完整分析工作流", _run)


def job_check_alerts() -> None:
    """定时: 检查告警"""
    def _run():
        from cryptobot.cli.monitor import _build_position_alerts, _build_signal_only_alerts
        from cryptobot.freqtrade_api import ft_api_get
        from cryptobot.signal.bridge import read_signals

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

    _run_with_retry("告警检查", _run)


def job_re_review() -> None:
    """定时: 持仓复审"""
    def _run():
        from cryptobot.freqtrade_api import ft_api_get
        from cryptobot.signal.bridge import update_signal_field

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

    _run_with_retry("持仓复审", _run)


def job_cleanup() -> None:
    """定时: 清理过期信号 + 过期缓存"""
    from cryptobot.signal.bridge import cleanup_expired

    try:
        expired = cleanup_expired()
        if expired:
            logger.info("[调度] 清理过期信号: %d 个", len(expired))
            from cryptobot.notify import notify_signal_expired
            for s in expired:
                notify_signal_expired(s.get("symbol", "?"), s.get("action", "?"))
    except Exception as e:
        logger.error("[调度] 信号清理失败: %s", e, exc_info=True)

    try:
        from cryptobot.cache import cleanup_stale
        cache_removed = cleanup_stale(max_age_hours=72)
        if cache_removed:
            logger.info("[调度] 清理过期缓存: %d 个文件", cache_removed)
    except Exception as e:
        logger.error("[调度] 缓存清理失败: %s", e, exc_info=True)


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


def job_urgent_review() -> None:
    """P&L 分级紧急复审: 亏损>3% 或盈利>10% 时立即复审"""
    def _run():
        from cryptobot.freqtrade_api import ft_api_get
        from cryptobot.workflow.graph import collect_data_for_symbols, re_review
        from cryptobot.signal.bridge import update_signal_field

        positions = ft_api_get("/status") or []

        for pos in positions:
            pnl_pct = (pos.get("profit_ratio", 0) or 0) * 100
            pair = pos.get("pair", "")
            symbol = pair.replace("/", "").replace(":USDT", "")
            if pnl_pct < -3 or pnl_pct > 10:
                logger.info("紧急复审触发: %s P&L=%.1f%%", symbol, pnl_pct)
                state = collect_data_for_symbols([symbol])
                suggestions = re_review([pos], state)
                for s in suggestions:
                    if s["decision"] == "adjust_stop_loss" and s.get("new_stop_loss"):
                        updated = update_signal_field(
                            s["symbol"], "stop_loss", s["new_stop_loss"],
                        )
                        if updated:
                            logger.info(
                                "[调度] 紧急复审更新 %s 止损 → %s",
                                s["symbol"], s["new_stop_loss"],
                            )

    _run_with_retry("紧急复审", _run)


def job_strategy_advisor() -> None:
    """定时: 策略顾问每日分析"""
    from cryptobot.evolution.strategy_advisor import run_advisor_cycle

    try:
        result = run_advisor_cycle()
        if result["triggered"]:
            logger.info("[调度] 策略顾问: %s", result["reason"])
        else:
            logger.debug("[调度] 策略顾问: %s", result["reason"])
    except Exception as e:
        logger.error("[调度] 策略顾问失败: %s", e, exc_info=True)


def job_overfit_check() -> None:
    """定时: 每周过拟合检查"""
    from cryptobot.evolution.overfit_detector import detect_overfit

    try:
        report = detect_overfit(30)
        if report.overfit_score >= 50:
            logger.warning(
                "[调度] 过拟合检测: score=%.0f -- %s",
                report.overfit_score,
                report.recommendation,
            )
            from cryptobot.notify import send_message

            signals_text = "\n".join(
                f"  - {s}" for s in report.signals
            )
            send_message(
                f"\u26a0\ufe0f 过拟合检测"
                f" (score={report.overfit_score:.0f})\n\n"
                f"{signals_text}\n\n"
                f"建议: {report.recommendation}"
            )
        else:
            logger.info(
                "[调度] 过拟合检测: score=%.0f, 正常",
                report.overfit_score,
            )
    except Exception as e:
        logger.error(
            "[调度] 过拟合检测失败: %s", e, exc_info=True,
        )


def job_volatile_toggle() -> None:
    """定时: Volatile 策略自适应评估"""
    from cryptobot.evolution.volatile_toggle import evaluate_toggle

    try:
        state = evaluate_toggle()
        logger.info(
            "[调度] volatile_toggle 评估: enabled=%s, observe=%d, loss_streak=%d",
            state.enabled, state.consecutive_observe, state.subtype_loss_streak,
        )
    except Exception as e:
        logger.error("[调度] volatile_toggle 评估失败: %s", e, exc_info=True)


def job_ml_retrain() -> None:
    """定时: ML 模型重训"""
    def _run():
        from cryptobot.ml.retrainer import run_retrain

        result = run_retrain()
        if result.action == "skipped":
            logger.info("[调度] ML 重训跳过: %s", result.reason)
        elif result.action == "rolled_back":
            logger.warning("[调度] ML 重训回滚: %s", result.reason)
            from cryptobot.notify import send_message
            send_message(f"ML 模型重训回滚: {result.reason}")
        else:
            logger.info(
                "[调度] ML 重训完成: %s (%s)", result.version, result.action,
            )
            from cryptobot.notify import send_message
            auc = result.metrics.get("auc_roc", 0)
            send_message(f"ML 模型重训: {result.version} (AUC={auc:.4f})")

    _run_with_retry("ML 模型重训", _run)


def job_journal_sync() -> None:
    """定时: 同步 Freqtrade 平仓数据到交易日志"""
    def _run():
        from cryptobot.journal.storage import get_records_by_status, update_record
        from cryptobot.freqtrade_api import ft_api_get

        resp = ft_api_get("/trades") or {}
        trades = resp.get("trades", []) if isinstance(resp, dict) else resp
        closed_trades = [t for t in trades if t.get("is_open") is False]
        active_records = get_records_by_status("active")

        synced = 0
        for record in active_records:
            ft_pair = _symbol_to_ft_pair(record.symbol)
            for trade in closed_trades:
                if trade.get("pair") != ft_pair:
                    continue
                if trade.get("is_short", False) != (record.action == "short"):
                    continue
                # 时间窗口: trade 的开仓时间必须在 record 之后
                open_date = trade.get("open_date", "")
                if open_date:
                    from datetime import datetime
                    try:
                        open_dt = datetime.fromisoformat(open_date[:19])
                        record_dt = datetime.fromisoformat(record.timestamp[:19])
                        if record_dt > open_dt:
                            continue
                    except (ValueError, TypeError):
                        continue

                pnl_pct = (trade.get("profit_ratio", 0) or 0) * 100
                pnl_usdt = trade.get("profit_abs", 0) or 0

                # O26: 补全 exit_reason + duration_hours
                exit_reason = trade.get("exit_reason", "")
                duration_hours = None
                open_date_str = trade.get("open_date", "")
                close_date_str = trade.get("close_date", "")
                if open_date_str and close_date_str:
                    try:
                        from datetime import datetime
                        fmt = "%Y-%m-%d %H:%M:%S"
                        od = datetime.strptime(open_date_str[:19], fmt)
                        cd = datetime.strptime(close_date_str[:19], fmt)
                        duration_hours = round((cd - od).total_seconds() / 3600, 1)
                    except (ValueError, TypeError):
                        pass

                update_record(
                    record.signal_id,
                    status="closed",
                    actual_entry_price=trade.get("open_rate"),
                    actual_exit_price=trade.get("close_rate"),
                    actual_pnl_pct=round(pnl_pct, 2),
                    actual_pnl_usdt=round(pnl_usdt, 2),
                    exit_reason=exit_reason,
                    duration_hours=duration_hours,
                )
                synced += 1
                # 推送交易关闭通知
                from cryptobot.notify import notify_trade_closed
                notify_trade_closed({
                    "symbol": record.symbol,
                    "action": record.action,
                    "pnl_pct": round(pnl_pct, 2),
                    "entry_price": trade.get("open_rate"),
                    "close_price": trade.get("close_rate"),
                    "leverage": record.leverage,
                })
                break

        if synced:
            logger.info("[调度] 交易日志同步: %d 笔", synced)

    _run_with_retry("交易日志同步", _run)


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

    if not _check_pid_file():
        console.print("[red]调度器已在运行中 (PID 文件存在且进程存活)[/red]")
        raise SystemExit(1)
    _write_pid_file()

    settings = load_settings()
    schedule_cfg = settings.get("schedule", {})
    # 支持 full_cycle_minutes (优先) 或 full_cycle_hours (向后兼容)
    full_cycle_min = schedule_cfg.get("full_cycle_minutes")
    if full_cycle_min is None:
        full_cycle_min = schedule_cfg.get("full_cycle_hours", 2) * 60
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
        minutes=full_cycle_min,
        id="workflow_run",
        name=f"完整分析 (每{full_cycle_min}min)",
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

    # 紧急复审检查: 每 30min
    scheduler.add_job(
        job_urgent_review,
        "interval",
        minutes=30,
        id="urgent_review",
        name="紧急复审检查 (每30min)",
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

    # 每日策略顾问: UTC 9:00 (在 prompt_optimization 之后 1 小时)
    scheduler.add_job(
        job_strategy_advisor,
        "cron",
        hour=9, minute=0,
        id="strategy_advisor",
        name="策略顾问 (每日9:00)",
        max_instances=1,
    )

    # 每日 Volatile 策略评估: UTC 12:00
    scheduler.add_job(
        job_volatile_toggle,
        "cron",
        hour=12, minute=0,
        id="volatile_toggle",
        name="Volatile 策略评估 (每日12:00)",
        max_instances=1,
    )

    # 每周过拟合检查: UTC 每周一 10:00
    scheduler.add_job(
        job_overfit_check,
        "cron",
        day_of_week="mon",
        hour=10,
        minute=0,
        id="overfit_check",
        name="过拟合检查 (每周一10:00)",
        max_instances=1,
    )

    # 每周 ML 模型重训: UTC 每周日 6:00
    scheduler.add_job(
        job_ml_retrain,
        "cron",
        day_of_week="sun",
        hour=6,
        minute=0,
        id="ml_retrain",
        name="ML 模型重训 (每周日6:00)",
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
    with _config_lock:
        try:
            _last_mtime = os.path.getmtime(_CONFIG_PATH)
            import yaml
            with open(_CONFIG_PATH) as f:
                _last_config = yaml.safe_load(f) or {}
        except OSError:
            pass

    console.print("[cyan]调度器启动[/cyan]")
    console.print(f"  完整分析: 每 {full_cycle_min}min")
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

    # Telegram Bot 长轮询 (后台线程)
    from cryptobot.telegram.bot import start_bot_thread
    bot_thread = start_bot_thread()
    if bot_thread:
        console.print("  Telegram Bot: 已启用 (长轮询)")
    else:
        console.print("  Telegram Bot: 未启用")

    console.print("\n按 Ctrl+C 停止\n")

    # 启动时立即运行一次告警检查
    job_check_alerts()

    if run_now:
        console.print("[cyan]立即运行一次完整分析...[/cyan]")
        scheduler.add_job(job_workflow_run, "date", id="run_now")

    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        stop_event.set()
        _remove_pid_file()
        console.print("\n[yellow]调度器已停止[/yellow]")
