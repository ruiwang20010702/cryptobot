"""CLI: 回测评估命令"""

import json

import click
from rich.console import Console
from rich.table import Table

console = Console()


@click.group()
def backtest():
    """信号回测与评估"""
    pass


@backtest.command("evaluate")
@click.option("--days", default=30, help="评估天数")
@click.option("--json-output", is_flag=True, help="JSON 输出")
def evaluate(days: int, json_output: bool):
    """评估历史信号质量"""
    from cryptobot.backtest.evaluator import evaluate_signals

    result = evaluate_signals(days)
    overview = result.get("overview", {})

    if json_output:
        click.echo(json.dumps(result, indent=2, ensure_ascii=False))
        return

    if overview.get("total", 0) == 0:
        console.print(f"[yellow]近 {days} 天无已平仓信号[/yellow]")
        return

    # 总览
    console.print(f"\n[bold]信号评估报告 (近 {days} 天)[/bold]\n")

    table = Table(title="总览")
    table.add_column("指标", style="cyan")
    table.add_column("值", justify="right")
    table.add_row("总交易", str(overview["total"]))
    table.add_row("胜/负", f"{overview['wins']} / {overview['losses']}")
    table.add_row("胜率", f"{overview['win_rate'] * 100:.1f}%")
    table.add_row("平均盈亏", f"{overview['avg_pnl_pct']:+.2f}%")
    table.add_row("最佳", f"{overview['best_trade_pct']:+.2f}%")
    table.add_row("最差", f"{overview['worst_trade_pct']:+.2f}%")
    table.add_row("累计 PnL", f"{overview['total_pnl_usdt']:+.0f} USDT")
    console.print(table)

    # 按币种
    by_symbol = result.get("by_symbol", {})
    if by_symbol:
        sym_table = Table(title="按币种")
        sym_table.add_column("币种")
        sym_table.add_column("笔数", justify="right")
        sym_table.add_column("胜率", justify="right")
        sym_table.add_column("平均盈亏", justify="right")
        sym_table.add_column("PnL USDT", justify="right")
        for sym, stats in by_symbol.items():
            sym_table.add_row(
                sym, str(stats["count"]),
                f"{stats['win_rate'] * 100:.0f}%",
                f"{stats['avg_pnl_pct']:+.1f}%",
                f"{stats['total_pnl_usdt']:+.0f}",
            )
        console.print(sym_table)

    # 盈亏比
    rr = result.get("risk_reward", {})
    if rr:
        console.print("\n[bold]实际盈亏比[/bold]")
        console.print(f"  平均盈利: +{rr.get('avg_win_pct', 0):.2f}%")
        console.print(f"  平均亏损: -{rr.get('avg_loss_pct', 0):.2f}%")
        console.print(f"  盈亏比: {rr.get('actual_risk_reward', 0)}")

    # 连胜连败
    streak = result.get("streak", {})
    if streak:
        console.print(f"\n  最大连胜: {streak.get('max_consecutive_wins', 0)}")
        console.print(f"  最大连败: {streak.get('max_consecutive_losses', 0)}")


@backtest.command("ab-test")
@click.option("--days", default=90, help="回溯天数")
@click.option("--json-output", is_flag=True, help="JSON 输出")
def ab_test(days: int, json_output: bool):
    """Prompt A/B 测试: 按 prompt_version 对比绩效"""
    from cryptobot.backtest.ab_test import run_ab_test

    result = run_ab_test(days)

    if json_output:
        click.echo(json.dumps(result, indent=2, ensure_ascii=False))
        return

    total = result["total_samples"]
    if total == 0:
        console.print(f"[yellow]近 {days} 天无已平仓交易记录[/yellow]")
        return

    console.print(f"\n[bold]Prompt A/B 测试 (近 {days} 天, {total} 笔)[/bold]\n")

    table = Table(title="按 Prompt 版本")
    table.add_column("版本")
    table.add_column("笔数", justify="right")
    table.add_column("胜率", justify="right")
    table.add_column("平均盈亏", justify="right")
    table.add_column("盈亏比", justify="right")

    for version, stats in result["versions"].items():
        pf = str(stats["profit_factor"]) if stats["profit_factor"] != float("inf") else "∞"
        table.add_row(
            version,
            str(stats["count"]),
            f"{stats['win_rate'] * 100:.1f}%",
            f"{stats['avg_pnl_pct']:+.2f}%",
            pf,
        )

    console.print(table)


@backtest.command("replay")
@click.argument("signal_id")
def replay(signal_id: str):
    """对单个信号进行 K 线复盘"""
    from cryptobot.journal.storage import get_record
    from cryptobot.backtest.evaluator import replay_signal

    record = get_record(signal_id)
    if not record:
        console.print(f"[red]信号 {signal_id} 不存在[/red]")
        return

    result = replay_signal(record)
    if not result:
        console.print("[yellow]无法获取 K 线数据进行复盘[/yellow]")
        return

    console.print(f"\n[bold]信号复盘: {result['symbol']} {result['action']}[/bold]")
    console.print(f"  入场中位: {result['entry_mid']}")
    console.print(f"  最大有利偏移 (MFE): +{result['mfe_pct']:.2f}%")
    console.print(f"  最大不利偏移 (MAE): -{result['mae_pct']:.2f}%")
    console.print(f"  止损触发: {'是' if result['sl_hit'] else '否'}")
    console.print(f"  止盈触发: {result['tp_hits']}/{result['tp_total']}")
    console.print(f"  分析 K 线: {result['bars_analyzed']} 根 (1h)")
