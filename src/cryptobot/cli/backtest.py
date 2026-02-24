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
    if interval <= 0:
        console.print("[red]Error: interval 须大于 0[/red]")
        return

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


@backtest.command("features")
@click.option("--json-output", is_flag=True, help="JSON 输出")
def features(json_output: bool):
    """查看最新特征矩阵"""
    from cryptobot.features.feature_store import load_latest_features
    from cryptobot.features.pipeline import to_csv_rows

    matrix = load_latest_features()
    if not matrix:
        console.print("[yellow]无特征数据[/yellow]")
        return

    if json_output:
        rows = to_csv_rows(matrix)
        click.echo(json.dumps(rows, indent=2, ensure_ascii=False))
        return

    console.print(
        f"\n[bold]最新特征矩阵[/bold] ({len(matrix.vectors)} 币种, "
        f"{len(matrix.feature_names)} 维特征)\n"
    )

    table = Table(title="特征矩阵")
    table.add_column("币种", style="cyan")
    for name in matrix.feature_names:
        table.add_column(name, justify="right")

    for vec in matrix.vectors:
        row = [vec.symbol]
        for name in matrix.feature_names:
            val = vec.features.get(name, 0.0)
            row.append(f"{val:.4f}")
        table.add_row(*row)

    console.print(table)


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


@backtest.command("analyze-replay")
@click.option("--report", default="", help="指定报告文件名(如 bt_20260222_xxx.json)")
@click.option("--json-output", is_flag=True, help="JSON 输出")
def analyze_replay(report: str, json_output: bool):
    """分析回放报告: 置信度分层 + 方向偏差 + 回撤控制"""
    from dataclasses import asdict
    from cryptobot.backtest.replay_analyzer import analyze_replay as do_analyze
    from cryptobot.backtest.trade_simulator import TradeResult
    from cryptobot.config import DATA_OUTPUT_DIR

    bt_dir = DATA_OUTPUT_DIR / "backtest"

    # 加载报告
    if report:
        path = bt_dir / report
    else:
        files = sorted(bt_dir.glob("bt_*.json"), reverse=True) if bt_dir.exists() else []
        if not files:
            console.print("[yellow]无回测报告文件[/yellow]")
            return
        path = files[0]

    if not path.exists():
        console.print(f"[red]报告不存在: {path}[/red]")
        return

    console.print(f"加载报告: {path.name}")
    data = json.loads(path.read_text())
    trades_raw = data.get("trades_summary", [])

    if not trades_raw:
        console.print("[yellow]报告中无交易明细 (trades_summary 为空)[/yellow]")
        return

    # 构造 TradeResult 列表
    trades = []
    for t in trades_raw:
        try:
            trades.append(TradeResult(**t))
        except (TypeError, KeyError):
            continue

    if not trades:
        console.print("[yellow]无法解析交易数据，可能报告格式旧 (缺少字段)[/yellow]")
        return

    console.print(f"解析 {len(trades)} 笔交易\n")

    result = do_analyze(trades)

    if json_output:
        click.echo(json.dumps(asdict(result), indent=2, ensure_ascii=False, default=str))
        return

    # 1. 置信度分层表
    conf_table = Table(title="置信度分层")
    conf_table.add_column("区间")
    conf_table.add_column("笔数", justify="right")
    conf_table.add_column("胜率", justify="right")
    conf_table.add_column("平均盈亏", justify="right")
    conf_table.add_column("盈亏比", justify="right")
    conf_table.add_column("平均杠杆", justify="right")
    for bucket, stats in result.confidence_buckets.items():
        if stats["count"] == 0:
            continue
        pf = f"{stats['profit_factor']:.2f}" if stats["profit_factor"] != float("inf") else "∞"
        conf_table.add_row(
            bucket, str(stats["count"]),
            f"{stats['win_rate'] * 100:.1f}%",
            f"{stats['avg_pnl_pct']:+.2f}%",
            pf, str(stats["avg_leverage"]),
        )
    console.print(conf_table)

    # 2. 方向分析表
    dir_summary = result.direction_analysis.get("summary", {})
    if dir_summary:
        dir_table = Table(title="方向分析")
        dir_table.add_column("方向")
        dir_table.add_column("笔数", justify="right")
        dir_table.add_column("占比", justify="right")
        dir_table.add_column("胜率", justify="right")
        dir_table.add_column("平均盈亏", justify="right")
        dir_table.add_column("总PnL USDT", justify="right")
        for action, stats in dir_summary.items():
            dir_table.add_row(
                action, str(stats["count"]),
                f"{stats['ratio'] * 100:.1f}%",
                f"{stats['win_rate'] * 100:.1f}%",
                f"{stats['avg_pnl_pct']:+.2f}%",
                f"{stats['total_pnl_usdt']:+.0f}",
            )
        console.print(dir_table)

        bias = result.direction_analysis.get("direction_bias", 0)
        dominant = result.direction_analysis.get("dominant_direction", "")
        if dominant:
            console.print(f"  主导方向: {dominant} | 偏差度: {bias:.2f}")

    # 2b. P17: 月×方向交叉表
    monthly_trend = result.direction_analysis.get("monthly_trend", {})
    if monthly_trend:
        mt_table = Table(title="月×方向交叉表")
        mt_table.add_column("月份")
        mt_table.add_column("方向")
        mt_table.add_column("笔数", justify="right")
        mt_table.add_column("胜率", justify="right")
        mt_table.add_column("平均盈亏", justify="right")
        mt_table.add_column("总PnL USDT", justify="right")
        for month, dirs in monthly_trend.items():
            for action, stats in sorted(dirs.items()):
                pnl_style = "green" if stats.get("total_pnl_usdt", 0) >= 0 else "red"
                mt_table.add_row(
                    month, action,
                    str(stats.get("count", 0)),
                    f"{stats.get('win_rate', 0) * 100:.0f}%",
                    f"{stats.get('avg_pnl_pct', 0):+.2f}%",
                    f"[{pnl_style}]{stats.get('total_pnl_usdt', 0):+.0f}[/{pnl_style}]",
                )
        console.print(mt_table)

    # 2c. P17: 置信度×方向交叉表
    conf_dir = result.direction_analysis.get("confidence_direction", {})
    if conf_dir:
        cd_table = Table(title="置信度×方向交叉表")
        cd_table.add_column("置信度区间")
        cd_table.add_column("方向")
        cd_table.add_column("笔数", justify="right")
        cd_table.add_column("胜率", justify="right")
        cd_table.add_column("平均盈亏", justify="right")
        cd_table.add_column("总PnL USDT", justify="right")
        for bucket, dirs in conf_dir.items():
            for action, stats in sorted(dirs.items()):
                pnl_style = "green" if stats.get("total_pnl_usdt", 0) >= 0 else "red"
                cd_table.add_row(
                    bucket, action,
                    str(stats.get("count", 0)),
                    f"{stats.get('win_rate', 0) * 100:.0f}%",
                    f"{stats.get('avg_pnl_pct', 0):+.2f}%",
                    f"[{pnl_style}]{stats.get('total_pnl_usdt', 0):+.0f}[/{pnl_style}]",
                )
        console.print(cd_table)

    # 2d. P17: 杠杆×方向交叉表
    lev_dir = result.direction_analysis.get("leverage_direction", {})
    if lev_dir:
        ld_table = Table(title="杠杆×方向交叉表")
        ld_table.add_column("杠杆区间")
        ld_table.add_column("方向")
        ld_table.add_column("笔数", justify="right")
        ld_table.add_column("胜率", justify="right")
        ld_table.add_column("平均盈亏", justify="right")
        ld_table.add_column("总PnL USDT", justify="right")
        for lev_label, dirs in lev_dir.items():
            for action, stats in sorted(dirs.items()):
                pnl_style = "green" if stats.get("total_pnl_usdt", 0) >= 0 else "red"
                ld_table.add_row(
                    lev_label, action,
                    str(stats.get("count", 0)),
                    f"{stats.get('win_rate', 0) * 100:.0f}%",
                    f"{stats.get('avg_pnl_pct', 0):+.2f}%",
                    f"[{pnl_style}]{stats.get('total_pnl_usdt', 0):+.0f}[/{pnl_style}]",
                )
        console.print(ld_table)

    # 3. 回撤控制模拟表
    if result.drawdown_simulation:
        dd_table = Table(title="回撤控制模拟")
        dd_table.add_column("策略")
        dd_table.add_column("总收益", justify="right")
        dd_table.add_column("最大回撤", justify="right")
        dd_table.add_column("Sharpe", justify="right")
        dd_table.add_column("Calmar", justify="right")
        dd_table.add_column("执行/跳过", justify="right")
        for name, sim in result.drawdown_simulation.items():
            dd_table.add_row(
                name,
                f"{sim['total_return_pct']:+.1f}%",
                f"{sim['max_drawdown_pct']:.1f}%",
                f"{sim['sharpe']:.2f}",
                f"{sim['calmar']:.2f}",
                f"{sim['trades_taken']}/{sim['trades_skipped']}",
            )
        console.print(dd_table)

    # 4. 优化建议
    if result.recommendations:
        console.print("\n[bold]优化建议[/bold]")
        for i, rec in enumerate(result.recommendations, 1):
            console.print(f"  {i}. {rec}")
    else:
        console.print("\n[green]未发现显著问题[/green]")


@backtest.command("overfit-check")
@click.option("--days", default=30, help="回溯天数")
@click.option("--json-output", is_flag=True, help="JSON 输出")
def overfit_check(days: int, json_output: bool):
    """过拟合检测"""
    from dataclasses import asdict

    from cryptobot.evolution.overfit_detector import detect_overfit

    report = detect_overfit(days)

    if json_output:
        click.echo(
            json.dumps(asdict(report), indent=2, ensure_ascii=False)
        )
        return

    # 颜色表示风险等级
    if report.overfit_score >= 70:
        score_style = "red bold"
    elif report.overfit_score >= 40:
        score_style = "yellow"
    else:
        score_style = "green"

    console.print(f"\n[bold]过拟合检测 (近 {days} 天)[/bold]\n")
    console.print(
        f"  过拟合分数: [{score_style}]"
        f"{report.overfit_score:.0f}/100[/{score_style}]"
    )

    # 修改频率
    freq = report.modification_frequency
    console.print("\n  近7天修改:")
    console.print(
        f"    Prompt 迭代: {freq.get('iterations_7d', 0)} 次"
    )
    console.print(
        f"    策略规则: {freq.get('strategy_rules_7d', 0)} 次"
    )
    console.print(
        f"    Prompt 版本: {freq.get('prompt_versions_7d', 0)} 次"
    )

    # 信号列表
    if report.signals:
        console.print("\n  [yellow]过拟合信号:[/yellow]")
        for s in report.signals:
            console.print(f"    - {s}")

    console.print(f"\n  建议: {report.recommendation}")


@backtest.command("replay-history")
@click.option("--days", default=90, help="回溯天数")
@click.option("--symbols", default="", help="币种列表(逗号分隔)，空=前5")
@click.option("--interval", default=24, help="采样间隔(小时)")
@click.option("--resume", is_flag=True, help="断点续跑")
@click.option(
    "--preset",
    type=click.Choice(["90d", "180d", "365d"]),
    default=None,
    help="预设周期",
)
@click.option("--json-output", is_flag=True, help="JSON 输出")
@click.option("--yes", "-y", is_flag=True, help="跳过确认")
def replay_history(
    days: int,
    symbols: str,
    interval: int,
    resume: bool,
    preset: str | None,
    json_output: bool,
    yes: bool,
):
    """历史回放: 用历史 K 线驱动 LLM 生成交易信号并回测"""
    if interval <= 0:
        console.print("[red]Error: interval 须大于 0[/red]")
        return

    from dataclasses import asdict
    from cryptobot.backtest.historical_replay import (
        ReplayConfig,
        run_historical_replay,
    )
    from cryptobot.backtest.engine import save_report

    _PRESET_MAP = {"90d": 90, "180d": 180, "365d": 365}
    if preset:
        days = _PRESET_MAP[preset]

    sym_list = (
        [s.strip() for s in symbols.split(",") if s.strip()]
        if symbols
        else []
    )
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


@backtest.command("walk-forward")
@click.option("--days", default=180, help="回溯天数")
@click.option(
    "--source", default="archive",
    type=click.Choice(["archive", "journal"]),
)
@click.option("--train-days", default=60, help="训练窗口天数")
@click.option("--test-days", default=30, help="测试窗口天数")
@click.option("--step-days", default=30, help="步进天数")
@click.option("--json-output", is_flag=True, help="JSON 输出")
def walk_forward(days, source, train_days, test_days, step_days, json_output):
    """Walk-forward 滚动验证 (防止过拟合)"""
    from dataclasses import asdict

    from cryptobot.backtest.engine import run_walk_forward_backtest

    console.print(
        f"\n[bold]Walk-forward 验证 "
        f"({days}天, {train_days}d/{test_days}d/{step_days}d)[/bold]\n"
    )

    result = run_walk_forward_backtest(
        days=days,
        source=source,
        train_days=train_days,
        test_days=test_days,
        step_days=step_days,
    )

    if json_output:
        click.echo(json.dumps(asdict(result), indent=2, ensure_ascii=False))
        return

    if not result.windows:
        console.print("[yellow]无足够数据运行 walk-forward[/yellow]")
        return

    # 窗口明细表
    table = Table(title="滚动窗口明细")
    table.add_column("#", justify="right")
    table.add_column("训练期")
    table.add_column("测试期")
    table.add_column("IS 笔数", justify="right")
    table.add_column("OOS 笔数", justify="right")
    table.add_column("IS 胜率", justify="right")
    table.add_column("OOS 胜率", justify="right")
    table.add_column("IS Sharpe", justify="right")
    table.add_column("OOS Sharpe", justify="right")

    for i, w in enumerate(result.windows, 1):
        table.add_row(
            str(i),
            f"{w.window.train_start[:10]}~{w.window.train_end[:10]}",
            f"{w.window.test_start[:10]}~{w.window.test_end[:10]}",
            str(w.train_trades),
            str(w.test_trades),
            f"{w.train_win_rate * 100:.1f}%",
            f"{w.test_win_rate * 100:.1f}%",
            f"{w.train_sharpe:.2f}",
            f"{w.test_sharpe:.2f}",
        )
    console.print(table)

    # 总结
    color = "green" if result.passed else "red"
    console.print(f"\n  IS 平均 Sharpe: {result.is_sharpe:.2f}")
    console.print(f"  OOS 平均 Sharpe: {result.oos_sharpe:.2f}")
    console.print(f"  IS/OOS 比率: {result.is_vs_oos_ratio:.2f}")
    console.print(f"  退化: {result.degradation_pct:.1f}%")
    console.print(f"  [{color}]{result.summary}[/{color}]")


@backtest.command("replay-compare")
@click.option("--json-output", is_flag=True, help="JSON 输出")
def replay_compare(json_output: bool):
    """对比多周期回放结果"""
    import json as json_mod
    from dataclasses import asdict

    from cryptobot.backtest.equity_tracker import BacktestMetrics
    from cryptobot.backtest.replay_comparator import (
        compare_replay_periods,
    )
    from cryptobot.config import DATA_OUTPUT_DIR

    bt_dir = DATA_OUTPUT_DIR / "backtest"
    if not bt_dir.exists():
        console.print("[yellow]无回测报告[/yellow]")
        return

    # 加载最近的回放报告 (按文件名匹配 bt_*.json)
    reports = []
    for f in sorted(bt_dir.glob("bt_*.json"), reverse=True)[:10]:
        try:
            data = json_mod.loads(f.read_text())
            if data.get("signal_source") != "replay":
                continue
            m_data = data.get("metrics", {})
            metrics = BacktestMetrics(**m_data)

            class _LightReport:
                pass

            r = _LightReport()
            r.metrics = metrics
            r.config = data.get("config", {})
            reports.append(r)
        except Exception:
            continue

    if len(reports) < 2:
        console.print(
            "[yellow]需要至少2个回放报告才能对比[/yellow]"
        )
        return

    comparison = compare_replay_periods(reports)

    if json_output:
        click.echo(
            json.dumps(
                asdict(comparison),
                indent=2,
                ensure_ascii=False,
            )
        )
        return

    console.print("\n[bold]多周期回放对比[/bold]\n")

    # 周期表
    table = Table(title="各周期指标")
    table.add_column("周期")
    table.add_column("交易数", justify="right")
    table.add_column("胜率", justify="right")
    table.add_column("Sharpe", justify="right")
    table.add_column("最大回撤", justify="right")
    table.add_column("总收益", justify="right")

    for p in comparison.periods:
        table.add_row(
            f"{p.days}天",
            str(p.total_trades),
            f"{p.win_rate * 100:.1f}%",
            f"{p.sharpe_ratio:.2f}",
            f"{p.max_drawdown_pct:.1f}%",
            f"{p.total_return_pct:+.1f}%",
        )
    console.print(table)

    # 稳定性
    grade_color = {
        "A": "green",
        "B": "cyan",
        "C": "yellow",
        "D": "red",
    }
    color = grade_color.get(comparison.stability_grade, "white")
    console.print(
        f"\n  稳定性等级: [{color}]"
        f"{comparison.stability_grade}[/{color}]"
    )
    console.print(f"  Sharpe CV: {comparison.sharpe_cv:.3f}")
    console.print(f"  胜率 CV: {comparison.win_rate_cv:.3f}")

    if comparison.warnings:
        console.print("\n  [yellow]警告:[/yellow]")
        for w in comparison.warnings:
            console.print(f"    - {w}")
