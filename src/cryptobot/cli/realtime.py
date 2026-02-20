"""实时入场监控 CLI 命令"""

import json
import logging
from datetime import datetime, timezone

import click
from rich.console import Console
from rich.table import Table

from cryptobot.signal.bridge import read_pending_signals

console = Console()


@click.group()
def realtime():
    """实时入场监控"""
    pass


@realtime.command("start")
@click.option("--log-level", default="INFO", help="日志级别")
def start(log_level: str):
    """启动实时入场监控（前台运行，Ctrl+C 停止）"""
    logging.basicConfig(
        level=getattr(logging, log_level.upper(), logging.INFO),
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
        datefmt="%H:%M:%S",
    )
    console.print("[cyan]启动实时入场监控...[/cyan]")
    console.print("[dim]Ctrl+C 停止[/dim]")

    from cryptobot.realtime.monitor import run_monitor

    run_monitor()


@realtime.command("status")
@click.option("--json-output", is_flag=True, help="输出 JSON 格式")
def status(json_output: bool):
    """显示当前 pending 信号列表和状态"""
    now = datetime.now(timezone.utc)
    pending = read_pending_signals(filter_expired=False)

    if json_output:
        click.echo(json.dumps(pending, indent=2, ensure_ascii=False))
        return

    if not pending:
        console.print("[yellow]当前无 pending 信号[/yellow]")
        return

    table = Table(title="Pending 信号")
    table.add_column("交易对", style="cyan")
    table.add_column("方向")
    table.add_column("入场区间")
    table.add_column("杠杆")
    table.add_column("置信度")
    table.add_column("过期时间")
    table.add_column("状态")

    for s in pending:
        action = s.get("action", "?")
        color = "green" if action == "long" else "red" if action == "short" else "white"

        entry_range = s.get("entry_price_range")
        if entry_range and entry_range[0] is not None:
            range_str = f"{entry_range[0]:.2f} - {entry_range[1]:.2f}"
        else:
            range_str = "-"

        expires = datetime.fromisoformat(s["expires_at"])
        expired = expires < now
        status_str = "[red]已过期[/red]" if expired else "[green]等待中[/green]"
        expires_str = expires.strftime("%m-%d %H:%M")

        table.add_row(
            s["symbol"],
            f"[{color}]{action.upper()}[/{color}]",
            range_str,
            f"{s.get('leverage', '?')}x",
            f"{s.get('confidence', '?')}%",
            expires_str,
            status_str,
        )

    console.print(table)
