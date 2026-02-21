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


@backtest.command("simulate")
@click.option("--days", default=14, help="模拟天数")
@click.option("--interval", default=12, help="分析间隔(小时)")
@click.option("--json-output", is_flag=True, help="JSON 输出")
@click.option("--yes", "-y", is_flag=True, help="跳过确认")
def simulate(days: int, interval: int, json_output: bool, yes: bool):
    """历史回放模拟: 用过去数据跑 AI 工作流并评估"""
    from cryptobot.backtest.simulator import run_simulation

    total_cycles = days * 24 // interval

    if not yes:
        click.confirm(
            f"模拟将运行 {total_cycles} 个周期 (约 {total_cycles * 45 // 60} 分钟)，确认?",
            abort=True,
        )

    console.print("\n[bold]历史回放模拟[/bold]")
    console.print(f"  回溯: {days} 天 | 间隔: {interval}h | 共 {total_cycles} 个周期\n")

    def on_cycle(idx, total, as_of, signals):
        n_sig = len(signals)
        ts = as_of.strftime("%Y-%m-%d %H:%M")
        console.print(f"  [{idx + 1}/{total}] {ts} — {n_sig} 信号")

    console.print("下载历史 K 线...")
    result = run_simulation(days=days, interval_hours=interval, on_cycle_done=on_cycle)

    if json_output:
        click.echo(json.dumps(result, indent=2, ensure_ascii=False, default=str))
        return

    overview = result.get("overview", {})
    total = overview.get("total", 0)
    console.print(f"\n模拟完成! 共 {total} 信号\n")

    if total == 0:
        console.print("[yellow]无信号生成[/yellow]")
        return

    console.print(f"总信号: {total}")
    console.print(f"止损触发: {overview.get('sl_hit', 0)} ({overview.get('sl_hit', 0) / total * 100:.0f}%)")
    tp_any = overview.get("tp_hit_any", 0)
    console.print(f"止盈触发: {tp_any} ({tp_any / total * 100:.0f}%)")
    console.print(f"胜率(MFE): {overview.get('win_rate_by_mfe', 0) * 100:.0f}%")
    console.print(f"平均 MFE: +{overview.get('avg_mfe_pct', 0):.1f}%")
    console.print(f"平均 MAE: -{overview.get('avg_mae_pct', 0):.1f}%")

    # 按币种
    by_symbol = result.get("by_symbol", {})
    if by_symbol:
        table = Table(title="按币种")
        table.add_column("币种")
        table.add_column("笔数", justify="right")
        table.add_column("胜率", justify="right")
        table.add_column("MFE", justify="right")
        table.add_column("MAE", justify="right")
        for sym, stats in by_symbol.items():
            table.add_row(
                sym, str(stats["count"]),
                f"{stats['win_rate'] * 100:.0f}%",
                f"+{stats['avg_mfe_pct']:.1f}%",
                f"-{stats['avg_mae_pct']:.1f}%",
            )
        console.print(table)

    # 按方向
    by_dir = result.get("by_direction", {})
    if by_dir:
        dir_table = Table(title="按方向")
        dir_table.add_column("方向")
        dir_table.add_column("笔数", justify="right")
        dir_table.add_column("胜率", justify="right")
        dir_table.add_column("MFE", justify="right")
        dir_table.add_column("MAE", justify="right")
        for d, stats in by_dir.items():
            dir_table.add_row(
                d, str(stats["count"]),
                f"{stats['win_rate'] * 100:.0f}%",
                f"+{stats['avg_mfe_pct']:.1f}%",
                f"-{stats['avg_mae_pct']:.1f}%",
            )
        console.print(dir_table)


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


@backtest.command("run")
@click.option("--days", default=90, help="回溯天数")
@click.option("--source", default="archive", type=click.Choice(["archive", "journal"]))
@click.option("--json-output", is_flag=True, help="JSON 输出")
def run(days: int, source: str, json_output: bool):
    """运行完整回测 (含成本模型)"""
    from dataclasses import asdict
    from cryptobot.backtest.engine import run_backtest, save_report

    console.print(f"\n[bold]量化回测 (近 {days} 天, 来源: {source})[/bold]\n")

    report = run_backtest(days=days, source=source)

    if json_output:
        data = {
            "config": report.config,
            "signal_source": report.signal_source,
            "total_signals_loaded": report.total_signals_loaded,
            "metrics": asdict(report.metrics),
            "by_symbol": report.by_symbol,
            "by_direction": report.by_direction,
        }
        click.echo(json.dumps(data, indent=2, ensure_ascii=False))
        return

    m = report.metrics
    if m.total_trades == 0:
        console.print(f"[yellow]近 {days} 天无可回测信号[/yellow]")
        return

    # 核心指标
    table = Table(title="回测指标")
    table.add_column("指标", style="cyan")
    table.add_column("值", justify="right")
    table.add_row("总交易", str(m.total_trades))
    table.add_row("胜率", f"{m.win_rate * 100:.1f}%")
    table.add_row("Sharpe", f"{m.sharpe_ratio:.2f}")
    table.add_row("Sortino", f"{m.sortino_ratio:.2f}")
    table.add_row("最大回撤", f"{m.max_drawdown_pct:.1f}%")
    table.add_row("Calmar", f"{m.calmar_ratio:.2f}")
    table.add_row("盈亏比 (PF)", f"{m.profit_factor:.2f}")
    table.add_row("总收益", f"{m.total_return_pct:+.1f}%")
    table.add_row("年化收益", f"{m.annualized_return_pct:+.1f}%")
    table.add_row("平均交易", f"{m.avg_trade_pnl_pct:+.2f}%")
    table.add_row("最佳交易", f"{m.best_trade_pct:+.2f}%")
    table.add_row("最差交易", f"{m.worst_trade_pct:+.2f}%")
    console.print(table)

    # 按币种
    if report.by_symbol:
        sym_table = Table(title="按币种")
        sym_table.add_column("币种")
        sym_table.add_column("笔数", justify="right")
        sym_table.add_column("胜率", justify="right")
        sym_table.add_column("平均盈亏", justify="right")
        for sym, stats in report.by_symbol.items():
            sym_table.add_row(
                sym, str(stats["count"]),
                f"{stats['win_rate'] * 100:.0f}%",
                f"{stats['avg_pnl_pct']:+.1f}%",
            )
        console.print(sym_table)

    # 保存报告
    path = save_report(report)
    console.print(f"\n报告已保存: {path}")


@backtest.command("baseline")
@click.option("--days", default=90, help="回溯天数")
@click.option(
    "--strategy", default="all",
    type=click.Choice(["all", "random", "ma_cross", "rsi", "bollinger"]),
)
@click.option("--json-output", is_flag=True, help="JSON 输出")
def baseline(days: int, strategy: str, json_output: bool):
    """运行基线策略回测"""
    from dataclasses import asdict
    from cryptobot.backtest.engine import run_baseline_backtest

    strategies = (
        ["random", "ma_cross", "rsi", "bollinger"]
        if strategy == "all"
        else [strategy]
    )

    results = {}
    for strat in strategies:
        console.print(f"  运行 {strat} 基线...")
        report = run_baseline_backtest(days=days, strategy=strat)
        results[strat] = report

    if json_output:
        data = {
            name: {
                "metrics": asdict(r.metrics),
                "total_signals": r.total_signals_loaded,
            }
            for name, r in results.items()
        }
        click.echo(json.dumps(data, indent=2, ensure_ascii=False))
        return

    console.print(f"\n[bold]基线回测 (近 {days} 天)[/bold]\n")

    table = Table(title="基线策略对比")
    table.add_column("策略")
    table.add_column("笔数", justify="right")
    table.add_column("胜率", justify="right")
    table.add_column("Sharpe", justify="right")
    table.add_column("MaxDD", justify="right")
    table.add_column("总收益", justify="right")

    for name, r in results.items():
        m = r.metrics
        table.add_row(
            name,
            str(m.total_trades),
            f"{m.win_rate * 100:.1f}%",
            f"{m.sharpe_ratio:.2f}",
            f"{m.max_drawdown_pct:.1f}%",
            f"{m.total_return_pct:+.1f}%",
        )

    console.print(table)


@backtest.command("compare")
@click.option("--days", default=90, help="回溯天数")
@click.option("--json-output", is_flag=True, help="JSON 输出")
def compare(days: int, json_output: bool):
    """AI vs 基线: 统计显著性对比"""
    from dataclasses import asdict
    from cryptobot.backtest.engine import run_backtest, run_baseline_backtest
    from cryptobot.backtest.stats import compare_with_baseline

    console.print(f"\n[bold]AI vs 基线对比 (近 {days} 天)[/bold]\n")

    # 1. AI 回测
    console.print("  运行 AI 回测...")
    ai_report = run_backtest(days=days)

    if ai_report.metrics.total_trades == 0:
        console.print("[yellow]无 AI 信号可回测[/yellow]")
        return

    # 2. 基线回测 + 对比
    baselines = ["random", "ma_cross", "rsi", "bollinger"]
    comparisons = []

    for strat in baselines:
        console.print(f"  运行 {strat} 基线...")
        bl_report = run_baseline_backtest(days=days, strategy=strat)
        if bl_report.metrics.total_trades == 0:
            continue
        cmp = compare_with_baseline(ai_report.trades, bl_report.trades, strat)
        comparisons.append(cmp)

    if json_output:
        data = {
            "ai_metrics": asdict(ai_report.metrics),
            "comparisons": [asdict(c) for c in comparisons],
        }
        click.echo(json.dumps(data, indent=2, ensure_ascii=False))
        return

    # 显示结果
    table = Table(title="AI vs 基线")
    table.add_column("基线")
    table.add_column("AI Sharpe", justify="right")
    table.add_column("基线 Sharpe", justify="right")
    table.add_column("AI 均值", justify="right")
    table.add_column("基线均值", justify="right")
    table.add_column("p-value", justify="right")
    table.add_column("显著?", justify="center")

    for c in comparisons:
        sig_mark = "[green]YES[/green]" if c.significant else "[red]NO[/red]"
        table.add_row(
            c.baseline_name,
            f"{c.ai_sharpe:.2f}",
            f"{c.baseline_sharpe:.2f}",
            f"{c.ai_mean_pnl:+.2f}%",
            f"{c.baseline_mean_pnl:+.2f}%",
            f"{c.pnl_p_value:.4f}",
            sig_mark,
        )

    console.print(table)

    # 总结
    all_sig = all(c.significant for c in comparisons) if comparisons else False
    if all_sig:
        console.print("\n[green bold]AI 信号在所有基线上均显著优于随机![/green bold]")
    else:
        failed = [c.baseline_name for c in comparisons if not c.significant]
        console.print(
            f"\n[yellow]AI 信号未显著优于: {', '.join(failed)}[/yellow]"
        )


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


@backtest.command("replay-history")
@click.option("--days", default=90, help="回溯天数")
@click.option("--symbols", default="", help="币种列表(逗号分隔)，空=前5")
@click.option("--interval", default=24, help="采样间隔(小时)")
@click.option("--resume", is_flag=True, help="断点续跑")
@click.option("--json-output", is_flag=True, help="JSON 输出")
@click.option("--yes", "-y", is_flag=True, help="跳过确认")
def replay_history(
    days: int, symbols: str, interval: int, resume: bool, json_output: bool, yes: bool,
):
    """历史回放: 用历史 K 线驱动 LLM 生成交易信号并回测"""
    from dataclasses import asdict
    from cryptobot.backtest.historical_replay import ReplayConfig, run_historical_replay
    from cryptobot.backtest.engine import save_report

    sym_list = [s.strip() for s in symbols.split(",") if s.strip()] if symbols else []
    config = ReplayConfig(
        days=days, symbols=sym_list, interval_hours=interval,
    )

    # 预估
    n_symbols = len(sym_list) if sym_list else 5
    total_points = days * 24 // interval if interval < 24 else days
    total_llm = total_points * n_symbols
    est_minutes = total_llm * 5 // 60  # ~5秒/调用

    if not yes:
        console.print("\n[bold]历史回放配置[/bold]")
        console.print(f"  回溯: {days} 天 | 间隔: {interval}h | 币种: {n_symbols}")
        console.print(f"  预估 LLM 调用: ~{total_llm} 次")
        console.print(f"  预估时间: ~{est_minutes} 分钟")
        click.confirm("确认开始?", abort=True)

    console.print(f"\n[bold]历史回放[/bold] ({days}天 × {n_symbols}币种)\n")

    def on_day(idx, total, date_str, n_sig):
        console.print(f"  [{idx + 1}/{total}] {date_str[:10]} — {n_sig} 个信号")

    report = run_historical_replay(config, resume=resume, on_day_done=on_day)

    if json_output:
        data = {
            "config": report.config,
            "signal_source": report.signal_source,
            "total_signals_loaded": report.total_signals_loaded,
            "metrics": asdict(report.metrics),
            "by_symbol": report.by_symbol,
            "by_direction": report.by_direction,
        }
        click.echo(json.dumps(data, indent=2, ensure_ascii=False))
        return

    m = report.metrics
    if m.total_trades == 0:
        console.print("[yellow]无有效交易产出[/yellow]")
        return

    # 核心指标
    table = Table(title="历史回放回测")
    table.add_column("指标", style="cyan")
    table.add_column("值", justify="right")
    table.add_row("总信号", str(report.total_signals_loaded))
    table.add_row("有效交易", str(m.total_trades))
    table.add_row("胜率", f"{m.win_rate * 100:.1f}%")
    table.add_row("Sharpe", f"{m.sharpe_ratio:.2f}")
    table.add_row("Sortino", f"{m.sortino_ratio:.2f}")
    table.add_row("最大回撤", f"{m.max_drawdown_pct:.1f}%")
    table.add_row("盈亏比 (PF)", f"{m.profit_factor:.2f}")
    table.add_row("总收益", f"{m.total_return_pct:+.1f}%")
    table.add_row("平均交易", f"{m.avg_trade_pnl_pct:+.2f}%")
    console.print(table)

    # 按币种
    if report.by_symbol:
        sym_table = Table(title="按币种")
        sym_table.add_column("币种")
        sym_table.add_column("笔数", justify="right")
        sym_table.add_column("胜率", justify="right")
        sym_table.add_column("平均盈亏", justify="right")
        for sym, stats in report.by_symbol.items():
            sym_table.add_row(
                sym, str(stats["count"]),
                f"{stats['win_rate'] * 100:.0f}%",
                f"{stats['avg_pnl_pct']:+.1f}%",
            )
        console.print(sym_table)

    # 按方向
    if report.by_direction:
        dir_table = Table(title="按方向")
        dir_table.add_column("方向")
        dir_table.add_column("笔数", justify="right")
        dir_table.add_column("胜率", justify="right")
        dir_table.add_column("平均盈亏", justify="right")
        for d, stats in report.by_direction.items():
            dir_table.add_row(
                d, str(stats["count"]),
                f"{stats['win_rate'] * 100:.0f}%",
                f"{stats['avg_pnl_pct']:+.1f}%",
            )
        console.print(dir_table)

    path = save_report(report)
    console.print(f"\n报告已保存: {path}")
