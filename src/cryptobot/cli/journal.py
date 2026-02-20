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
