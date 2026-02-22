"""风控命令"""

import json

import click
from rich.console import Console
from rich.table import Table

console = Console()


@click.group("risk")
def risk():
    """风控管理"""
    pass


@risk.command("symbol-profile")
@click.option("--days", default=180, help="回溯天数")
@click.option("--min-trades", default=15, help="最少交易笔数")
@click.option("--json-output", is_flag=True, help="JSON 输出")
def symbol_profile(days: int, min_trades: int, json_output: bool):
    """按币种差异化分级"""
    from dataclasses import asdict

    from cryptobot.risk.symbol_profile import grade_symbols

    result = grade_symbols(min_trades=min_trades, days=days)

    if json_output:
        click.echo(json.dumps(
            {"grades": [asdict(g) for g in result.grades], "updated_at": result.updated_at},
            indent=2, ensure_ascii=False,
        ))
        return

    table = Table(title=f"币种分级 (近{days}天, 最少{min_trades}笔)")
    table.add_column("币种", style="cyan")
    table.add_column("等级")
    table.add_column("胜率", justify="right")
    table.add_column("平均盈亏%", justify="right")
    table.add_column("交易数", justify="right")
    table.add_column("杠杆", justify="right")
    table.add_column("置信度+", justify="right")
    table.add_column("状态")

    grade_colors = {"A": "green", "B": "cyan", "C": "yellow", "D": "red"}
    for g in sorted(result.grades, key=lambda x: x.grade):
        color = grade_colors.get(g.grade, "white")
        status = "[red]禁止[/red]" if g.blocked else "[green]正常[/green]"
        table.add_row(
            g.symbol,
            f"[{color}]{g.grade}[/{color}]",
            f"{g.win_rate:.1%}" if g.trade_count > 0 else "-",
            f"{g.avg_pnl_pct:+.2f}%" if g.trade_count > 0 else "-",
            str(g.trade_count),
            str(g.recommended_leverage),
            f"+{g.min_confidence}" if g.min_confidence > 0 else "0",
            status,
        )

    console.print(table)
