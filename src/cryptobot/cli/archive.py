"""CLI: archive — AI 决策归档查阅"""

import json
import shutil
from datetime import datetime, timezone, timedelta
from pathlib import Path

import click
from rich.console import Console
from rich.table import Table

_console = Console()


@click.group()
def archive():
    """AI 决策归档管理"""


@archive.command("list")
@click.option("--month", default=None, help="指定月份 (如 2026-02)")
@click.option("--limit", default=20, help="最多显示条数")
def list_cmd(month: str | None, limit: int):
    """列出归档摘要"""
    from cryptobot.archive.reader import list_archives

    items = list_archives(month=month, limit=limit)
    if not items:
        _console.print("[yellow]暂无归档记录[/yellow]")
        return

    table = Table(title="决策归档列表")
    table.add_column("Run ID", style="cyan")
    table.add_column("时间", style="green")
    table.add_column("Regime", style="magenta")
    table.add_column("筛选", justify="right")
    table.add_column("决策", justify="right")
    table.add_column("通过", justify="right", style="green")
    table.add_column("错误", justify="right", style="red")

    for item in items:
        ts = item["timestamp"][:19].replace("T", " ") if item["timestamp"] else ""
        table.add_row(
            item["run_id"],
            ts,
            item["regime"],
            str(item["screened"]),
            str(item["decisions"]),
            str(item["approved"]),
            str(item["errors"]),
        )

    _console.print(table)


@archive.command("show")
@click.argument("run_id")
@click.option("--json-output", is_flag=True, help="JSON 格式输出")
def show_cmd(run_id: str, json_output: bool):
    """查看完整归档"""
    from cryptobot.archive.reader import get_archive

    data = get_archive(run_id)
    if not data:
        _console.print(f"[red]未找到归档: {run_id}[/red]")
        return

    if json_output:
        click.echo(json.dumps(data, ensure_ascii=False, indent=2))
        return

    # 摘要展示
    _console.print(f"\n[bold cyan]归档: {data['run_id']}[/bold cyan]")
    _console.print(f"时间: {data.get('timestamp', '')}")

    regime = data.get("regime", {})
    if regime:
        _console.print(
            f"Regime: {regime.get('regime', '?')} "
            f"(置信度 {regime.get('confidence', '?')})"
        )

    tier = data.get("capital_tier", {})
    if tier:
        _console.print(f"资金层级: {tier.get('tier', '?')} (${tier.get('balance', 0):.0f})")

    fg = data.get("fear_greed", {})
    if fg:
        _console.print(f"恐惧贪婪: {fg.get('current_value', '?')} ({fg.get('current_classification', '')})")

    # 筛选
    scores = data.get("screening_scores", [])
    if scores:
        _console.print(f"\n[bold]筛选评分[/bold] ({len(scores)} 币种)")
        for sym, score in scores[:10]:
            _console.print(f"  {sym}: {score}")

    # 决策
    decisions = data.get("decisions", [])
    if decisions:
        _console.print(f"\n[bold]交易决策[/bold] ({len(decisions)} 个)")
        for d in decisions:
            action = d.get("action", "?")
            sym = d.get("symbol", "?")
            conf = d.get("confidence", "?")
            color = "green" if action == "long" else "red" if action == "short" else "dim"
            _console.print(f"  [{color}]{sym} {action}[/{color}] 置信度={conf}")

    # 风控
    risk = data.get("risk_details", {})
    if risk:
        rejected = risk.get("rejected_signals", [])
        if rejected:
            _console.print(f"\n[bold]被拒信号[/bold] ({len(rejected)} 个)")
            for r in rejected:
                _console.print(f"  [red]{r.get('symbol', '?')}: {r.get('reason', '')}[/red]")

    # 通过
    approved = data.get("approved_signals", [])
    if approved:
        _console.print(f"\n[bold green]通过信号[/bold green] ({len(approved)} 个)")
        for s in approved:
            _console.print(f"  {s.get('symbol', '?')} {s.get('action', '?')} "
                           f"杠杆={s.get('leverage', '?')}x")

    # 错误
    errors = data.get("errors", [])
    if errors:
        _console.print(f"\n[bold red]错误[/bold red] ({len(errors)} 个)")
        for e in errors:
            _console.print(f"  {e}")


@archive.command("history")
@click.argument("symbol")
@click.option("--days", default=30, help="查询天数")
def history_cmd(symbol: str, days: int):
    """查看某币种的决策历史"""
    from cryptobot.archive.reader import get_symbol_history

    symbol = symbol.upper()
    items = get_symbol_history(symbol, days=days)
    if not items:
        _console.print(f"[yellow]{symbol} 最近 {days} 天无决策记录[/yellow]")
        return

    table = Table(title=f"{symbol} 决策历史 (最近 {days} 天)")
    table.add_column("Run ID", style="cyan")
    table.add_column("时间", style="green")
    table.add_column("Regime")
    table.add_column("筛选", justify="center")
    table.add_column("决策")
    table.add_column("通过", justify="center")

    for item in items:
        ts = item["timestamp"][:19].replace("T", " ") if item["timestamp"] else ""
        decision = item.get("decision")
        decision_str = ""
        if decision:
            decision_str = f"{decision.get('action', '?')} conf={decision.get('confidence', '?')}"
        table.add_row(
            item["run_id"],
            ts,
            item["regime"],
            "Y" if item.get("screened") else "",
            decision_str,
            "[green]Y[/green]" if item.get("approved") else "[red]N[/red]" if decision else "",
        )

    _console.print(table)


@archive.command("cleanup")
@click.option("--keep-months", default=3, help="保留最近几个月")
@click.confirmation_option(prompt="确认清理旧归档？")
def cleanup_cmd(keep_months: int):
    """清理旧归档"""
    archive_base = Path("data/output/archive")
    if not archive_base.exists():
        _console.print("[yellow]无归档目录[/yellow]")
        return

    cutoff = datetime.now(timezone.utc) - timedelta(days=keep_months * 30)
    cutoff_month = cutoff.strftime("%Y-%m")
    removed = 0

    for month_dir in sorted(archive_base.iterdir()):
        if not month_dir.is_dir():
            continue
        try:
            dir_date = datetime.strptime(month_dir.name, "%Y-%m")
            cutoff_date = datetime.strptime(cutoff_month, "%Y-%m")
        except ValueError:
            continue
        if dir_date < cutoff_date:
            count = len(list(month_dir.glob("*.json")))
            shutil.rmtree(month_dir)
            removed += count
            _console.print(f"  删除 {month_dir.name}/ ({count} 个文件)")

    _console.print(f"[green]清理完成: 删除 {removed} 个归档[/green]")
