"""持仓命令: Freqtrade REST API 封装"""

import json

import click
from rich.console import Console
from rich.table import Table
from rich.panel import Panel

from cryptobot.freqtrade_api import ft_api_get

console = Console()


@click.group()
def portfolio():
    """持仓查询"""
    pass


@portfolio.command()
@click.option("--json-output", is_flag=True, help="输出 JSON 格式")
def show(json_output: bool):
    """显示当前持仓"""
    data = ft_api_get("/status")
    if data is None:
        if json_output:
            click.echo(json.dumps({"error": "Freqtrade 未运行", "trades": []}, ensure_ascii=False))
        else:
            console.print("[yellow]Freqtrade 未运行或无法连接 (http://127.0.0.1:8080)[/yellow]")
            console.print("启动方式: freqtrade trade --config config/freqtrade/config.json --strategy AgentSignalStrategy")
        return

    if json_output:
        click.echo(json.dumps(data, indent=2, ensure_ascii=False))
        return

    if not data:
        console.print("[yellow]当前无持仓[/yellow]")
        return

    table = Table(title="当前持仓")
    table.add_column("交易对", style="cyan")
    table.add_column("方向")
    table.add_column("杠杆")
    table.add_column("入场价")
    table.add_column("当前价")
    table.add_column("盈亏%", justify="right")
    table.add_column("盈亏(USDT)", justify="right")
    table.add_column("止损")
    table.add_column("持仓时长")

    for t in data:
        pnl_pct = t.get("profit_pct", 0) * 100
        pnl_color = "green" if pnl_pct >= 0 else "red"
        direction = "LONG" if not t.get("is_short", False) else "SHORT"
        dir_color = "green" if direction == "LONG" else "red"

        table.add_row(
            t.get("pair", "?"),
            f"[{dir_color}]{direction}[/{dir_color}]",
            str(t.get("leverage", "-")),
            f"{t.get('open_rate', 0):.2f}",
            f"{t.get('current_rate', 0):.2f}",
            f"[{pnl_color}]{pnl_pct:+.2f}%[/{pnl_color}]",
            f"[{pnl_color}]{t.get('profit_abs', 0):+.2f}[/{pnl_color}]",
            f"{t.get('stop_loss_abs', 0):.2f}" if t.get("stop_loss_abs") else "-",
            t.get("trade_duration", "-"),
        )

    console.print(table)


@portfolio.command()
@click.option("--json-output", is_flag=True, help="输出 JSON 格式")
def summary(json_output: bool):
    """持仓摘要"""
    profit = ft_api_get("/profit")
    balance = ft_api_get("/balance")

    if profit is None or balance is None:
        if json_output:
            click.echo(json.dumps({"error": "Freqtrade 未运行"}, ensure_ascii=False))
        else:
            console.print("[yellow]Freqtrade 未运行或无法连接[/yellow]")
        return

    if json_output:
        click.echo(json.dumps({"profit": profit, "balance": balance}, indent=2, ensure_ascii=False))
        return

    lines = []
    if profit:
        lines.append(f"总盈亏: {profit.get('profit_all_coin', 0):.2f} USDT ({profit.get('profit_all_pct', 0):.2f}%)")
        lines.append(f"今日盈亏: {profit.get('profit_closed_coin', 0):.2f} USDT")
        lines.append(f"已关闭交易: {profit.get('trade_count', 0)} 笔")
        lines.append(f"胜率: {profit.get('winning_trades', 0)}/{profit.get('losing_trades', 0)}")

    if balance:
        for b in balance.get("currencies", []):
            if b.get("currency") == "USDT":
                lines.append(f"USDT 余额: {b.get('balance', 0):.2f}")
                lines.append(f"可用: {b.get('free', 0):.2f}")
                lines.append(f"冻结: {b.get('used', 0):.2f}")

    console.print(Panel("\n".join(lines) if lines else "无数据", title="账户摘要"))


@portfolio.command()
@click.option("--period", default="7d", help="时间范围 (如 7d, 30d)")
@click.option("--json-output", is_flag=True, help="输出 JSON 格式")
def pnl(period: str, json_output: bool):
    """盈亏统计"""
    data = ft_api_get("/performance")

    if data is None:
        if json_output:
            click.echo(json.dumps({"error": "Freqtrade 未运行"}, ensure_ascii=False))
        else:
            console.print("[yellow]Freqtrade 未运行或无法连接[/yellow]")
        return

    if json_output:
        click.echo(json.dumps(data, indent=2, ensure_ascii=False))
        return

    if not data:
        console.print("[yellow]暂无交易记录[/yellow]")
        return

    table = Table(title=f"盈亏统计 (请求范围: {period})")
    table.add_column("交易对", style="cyan")
    table.add_column("盈亏%", justify="right")
    table.add_column("交易次数", justify="right")

    for p in data:
        pnl_pct = p.get("profit_pct", 0)
        pnl_color = "green" if pnl_pct >= 0 else "red"
        table.add_row(
            p.get("pair", "?"),
            f"[{pnl_color}]{pnl_pct:+.2f}%[/{pnl_color}]",
            str(p.get("count", 0)),
        )

    console.print(table)
