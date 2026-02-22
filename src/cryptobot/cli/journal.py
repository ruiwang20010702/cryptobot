"""交易记录命令: 查看信号记录与绩效统计"""

import json

import click
from rich.console import Console
from rich.table import Table
from rich.panel import Panel

console = Console()


@click.group()
def journal():
    """交易记录与绩效"""
    pass


@journal.command("show")
@click.option("--status", type=click.Choice(["all", "pending", "active", "closed", "expired"]),
              default="all", help="按状态过滤")
@click.option("--limit", default=20, help="显示条数")
@click.option("--json-output", is_flag=True, help="输出 JSON 格式")
def show(status: str, limit: int, json_output: bool):
    """查看信号记录"""
    from cryptobot.journal.storage import get_all_records, get_records_by_status

    if status == "all":
        records = get_all_records()
    else:
        records = get_records_by_status(status)

    # 按时间倒序
    records.sort(key=lambda r: r.timestamp, reverse=True)
    records = records[:limit]

    if json_output:
        click.echo(json.dumps(
            [r.to_dict() for r in records],
            indent=2, ensure_ascii=False,
        ))
        return

    if not records:
        console.print("[yellow]无记录[/yellow]")
        return

    table = Table(title=f"信号记录 ({status})")
    table.add_column("ID", style="dim", max_width=12)
    table.add_column("时间", max_width=16)
    table.add_column("币种", style="cyan")
    table.add_column("方向")
    table.add_column("置信度", justify="right")
    table.add_column("状态")
    table.add_column("盈亏%", justify="right")
    table.add_column("退出原因")

    for r in records:
        dir_color = "green" if r.action == "long" else "red"
        status_color = {
            "pending": "dim", "active": "cyan", "closed": "green", "expired": "yellow",
        }.get(r.status, "white")

        pnl_str = ""
        if r.actual_pnl_pct is not None:
            pnl_color = "green" if r.actual_pnl_pct >= 0 else "red"
            pnl_str = f"[{pnl_color}]{r.actual_pnl_pct:+.2f}%[/{pnl_color}]"

        table.add_row(
            r.signal_id,
            r.timestamp[:16] if r.timestamp else "",
            r.symbol,
            f"[{dir_color}]{r.action.upper()}[/{dir_color}]",
            str(r.confidence),
            f"[{status_color}]{r.status}[/{status_color}]",
            pnl_str,
            r.exit_reason or "",
        )

    console.print(table)


@journal.command("stats")
@click.option("--days", default=30, help="统计天数")
@click.option("--json-output", is_flag=True, help="输出 JSON 格式")
def stats(days: int, json_output: bool):
    """绩效统计"""
    from cryptobot.journal.analytics import calc_performance

    perf = calc_performance(days)

    if json_output:
        click.echo(json.dumps(perf, indent=2, ensure_ascii=False))
        return

    lines = [
        f"统计周期: 近 {perf['period_days']} 天",
        f"总信号: {perf['total_signals']}",
        f"已入场: {perf['entered']}",
        f"已平仓: {perf['closed']}",
        f"已过期: {perf['expired']}",
        "",
        f"胜率: {perf['win_rate']:.1%}",
        f"平均盈亏: {perf['avg_pnl_pct']:+.2f}%",
        f"Profit Factor: {perf['profit_factor']}",
        f"总盈亏: {perf['total_pnl_usdt']:+.2f} USDT",
    ]

    # 方向统计
    for direction, d in perf["by_direction"].items():
        if d["count"] > 0:
            lines.append(f"  {direction.upper()}: {d['count']} 笔, 胜率 {d['win_rate']:.1%}")

    # 置信度校准
    cal = perf["confidence_calibration"]
    cal_lines = []
    for bucket, data in cal.items():
        if data["count"] > 0:
            wr = f"{data['actual_win_rate']:.1%}" if data["actual_win_rate"] is not None else "?"
            cal_lines.append(f"  {bucket}: {data['count']} 笔, 实际胜率 {wr}")
    if cal_lines:
        lines.append("")
        lines.append("置信度校准:")
        lines.extend(cal_lines)

    console.print(Panel("\n".join(lines), title="绩效统计"))


@journal.command("sync")
@click.option("--json-output", is_flag=True, help="输出 JSON 格式")
def sync(json_output: bool):
    """同步 Freqtrade 平仓数据到记录"""
    from cryptobot.journal.storage import get_records_by_status, update_record
    from cryptobot.freqtrade_api import ft_api_get

    # 获取 Freqtrade 已平仓交易
    trades = ft_api_get("/trades") or []
    closed_trades = [t for t in trades if t.get("is_open") is False]

    # 获取 active 记录
    active_records = get_records_by_status("active")

    synced = 0
    for record in active_records:
        # 匹配 Freqtrade 交易（按币种 + 方向）
        ft_pair = record.symbol[:3] + "/" + record.symbol[3:] + ":USDT"
        for trade in closed_trades:
            if trade.get("pair") != ft_pair:
                continue
            trade_is_short = trade.get("is_short", False)
            record_is_short = record.action == "short"
            if trade_is_short != record_is_short:
                continue

            pnl_pct = (trade.get("profit_ratio", 0) or 0) * 100
            pnl_usdt = trade.get("profit_abs", 0) or 0

            # 推断退出原因
            exit_reason = _infer_exit_reason(trade)

            # 计算持仓时长
            duration = None
            if trade.get("open_date") and trade.get("close_date"):
                from datetime import datetime
                try:
                    open_dt = datetime.fromisoformat(trade["open_date"])
                    close_dt = datetime.fromisoformat(trade["close_date"])
                    duration = (close_dt - open_dt).total_seconds() / 3600
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
                duration_hours=round(duration, 1) if duration else None,
            )
            synced += 1
            break

    result = {"synced": synced, "active_records": len(active_records)}
    if json_output:
        click.echo(json.dumps(result, ensure_ascii=False))
    else:
        console.print(f"同步完成: {synced} 笔交易已更新")


@journal.command("backfill")
@click.option("--days", default=180, help="回溯天数")
@click.option("--dry-run", is_flag=True, help="预览不写入")
@click.option("--symbol", multiple=True, help="指定币种 (可多次)")
@click.option("--json-output", is_flag=True, help="输出 JSON 格式")
def backfill(days: int, dry_run: bool, symbol: tuple, json_output: bool):
    """历史信号回填 (从 K 线生成模拟记录)"""
    from dataclasses import asdict

    from cryptobot.journal.backfill import run_backfill

    symbols = list(symbol) if symbol else None

    if not json_output:
        mode = "预览模式" if dry_run else "写入模式"
        console.print(f"[cyan]开始回填 ({mode}, {days} 天)...[/cyan]")

    result = run_backfill(days=days, symbols=symbols, dry_run=dry_run)

    if json_output:
        click.echo(json.dumps(asdict(result), indent=2, ensure_ascii=False))
        return

    lines = [
        f"生成: {result.total_generated} 笔",
        f"保存: {result.total_saved} 笔",
        f"跳过(已存在): {result.skipped_existing} 笔",
        f"胜率: {result.win_rate:.1%}",
        f"平均盈亏: {result.avg_pnl_pct:+.2f}%",
    ]

    if result.by_symbol:
        lines.append("")
        lines.append("按币种:")
        for sym, cnt in sorted(result.by_symbol.items()):
            lines.append(f"  {sym}: {cnt} 笔")

    if result.by_exit_reason:
        lines.append("")
        lines.append("按退出原因:")
        for reason, cnt in sorted(result.by_exit_reason.items()):
            lines.append(f"  {reason}: {cnt} 笔")

    if result.errors:
        lines.append("")
        lines.append("[red]错误:[/red]")
        for err in result.errors:
            lines.append(f"  {err}")

    console.print(Panel("\n".join(lines), title="回填结果"))


@journal.command("edge")
@click.option("--days", default=30, help="回溯天数")
@click.option("--ci", is_flag=True, help="显示置信区间")
@click.option("--json-output", is_flag=True, help="JSON 输出")
def edge(days: int, ci: bool, json_output: bool):
    """Edge 仪表盘: 期望值/SQN/R分布"""
    from dataclasses import asdict

    from cryptobot.journal.edge import calc_edge, detect_edge_decay

    metrics = calc_edge(days)
    decay = detect_edge_decay()

    if json_output:
        click.echo(json.dumps(
            {"metrics": asdict(metrics), "decay": decay},
            indent=2, ensure_ascii=False,
        ))
        return

    # 核心指标
    exp_color = "green" if metrics.expectancy_pct > 0 else "red"
    lines = [
        f"统计周期: 近 {days} 天",
        "",
        f"期望值: [{exp_color}]{metrics.expectancy_pct:+.4f}%[/{exp_color}]",
        f"Edge Ratio: {metrics.edge_ratio:.4f}",
        f"SQN: {metrics.sqn:.4f}",
    ]
    console.print(Panel("\n".join(lines), title="Edge 核心指标"))

    # R 分布
    r_lines = []
    for bucket, count in metrics.r_distribution.items():
        bar = "#" * count
        r_lines.append(f"  {bucket:>8s}: {count:3d} {bar}")
    if any(v > 0 for v in metrics.r_distribution.values()):
        console.print(Panel("\n".join(r_lines), title="R 倍数分布"))

    # Regime 分组
    if metrics.regime_edge:
        regime_lines = []
        for regime, data in metrics.regime_edge.items():
            regime_lines.append(
                f"  {regime}: {data['count']} 笔, "
                f"胜率 {data['win_rate']:.1%}, "
                f"期望值 {data['expectancy']:+.4f}%"
            )
        console.print(Panel("\n".join(regime_lines), title="Regime 分组"))

    # 7d vs 30d 对比
    rvb = metrics.recent_vs_baseline
    recent = rvb["recent_7d"]
    baseline = rvb["baseline_30d"]
    change = rvb["change"]
    cmp_lines = [
        f"  近 7d: {recent['count']} 笔, "
        f"期望值 {recent['expectancy']:+.4f}%, "
        f"胜率 {recent['win_rate']:.1%}",
        f"  基准 {days}d: {baseline['count']} 笔, "
        f"期望值 {baseline['expectancy']:+.4f}%, "
        f"胜率 {baseline['win_rate']:.1%}",
        f"  变化: 期望值 {change['expectancy']:+.1f}%, "
        f"胜率 {change['win_rate']:+.1f}%",
    ]
    console.print(Panel("\n".join(cmp_lines), title="近期 vs 基准"))

    # 衰减检测
    if decay["decaying"]:
        console.print(f"[red bold]{decay['warning']}[/red bold]")


@journal.command("regime-eval")
@click.option("--days-a", default=30, help="Period A 天数 (基准期)")
@click.option("--days-b", default=14, help="Period B 天数 (评估期)")
@click.option("--json-output", is_flag=True, help="输出 JSON 格式")
def regime_eval(days_a: int, days_b: int, json_output: bool):
    """Regime 感知绩效评估: 按市场状态分组对比"""
    from dataclasses import asdict
    from datetime import datetime, timedelta, timezone

    from cryptobot.journal.regime_evaluator import evaluate_by_regime
    from cryptobot.journal.storage import get_all_records

    now = datetime.now(timezone.utc)
    cutoff_a = (now - timedelta(days=days_a + days_b)).isoformat()
    cutoff_b = (now - timedelta(days=days_b)).isoformat()

    all_records = get_all_records()
    closed = [
        r for r in all_records
        if r.status == "closed" and r.actual_pnl_pct is not None
    ]

    records_a = [r for r in closed if cutoff_a <= r.timestamp < cutoff_b]
    records_b = [r for r in closed if r.timestamp >= cutoff_b]

    results = evaluate_by_regime(records_a, records_b)

    if json_output:
        click.echo(json.dumps(
            [asdict(r) for r in results],
            indent=2, ensure_ascii=False,
        ))
        return

    if not results:
        console.print("[yellow]无足够数据进行 regime 评估[/yellow]")
        return

    table = Table(title=f"Regime 感知评估 (A: {days_a}d前~{days_b}d前 vs B: 近{days_b}d)")
    table.add_column("Regime", style="cyan")
    table.add_column("A 样本", justify="right")
    table.add_column("A 胜率", justify="right")
    table.add_column("A Sharpe", justify="right")
    table.add_column("B 样本", justify="right")
    table.add_column("B 胜率", justify="right")
    table.add_column("B Sharpe", justify="right")
    table.add_column("改善%", justify="right")
    table.add_column("显著", justify="center")

    for r in results:
        imp_color = "green" if r.improvement_pct > 0 else "red"
        sig_str = "[green]Yes[/green]" if r.significant else "[dim]No[/dim]"
        table.add_row(
            r.regime,
            str(r.period_a["count"]),
            f"{r.period_a['win_rate']:.1%}",
            f"{r.period_a['sharpe']:.2f}",
            str(r.period_b["count"]),
            f"{r.period_b['win_rate']:.1%}",
            f"{r.period_b['sharpe']:.2f}",
            f"[{imp_color}]{r.improvement_pct:+.1f}%[/{imp_color}]",
            sig_str,
        )

    console.print(table)


def _infer_exit_reason(trade: dict) -> str:
    """从 Freqtrade 交易推断退出原因"""
    exit_reason = trade.get("exit_reason", "") or ""
    if "stop_loss" in exit_reason or "stoploss" in exit_reason:
        return "sl_hit"
    if "roi" in exit_reason or "custom_exit" in exit_reason:
        return "tp_hit"
    if "force" in exit_reason:
        return "manual"
    return exit_reason or "unknown"
