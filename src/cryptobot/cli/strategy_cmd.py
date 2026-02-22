"""策略命令: 资金费率套利 + 网格交易"""

import json

import click
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

console = Console()


@click.group()
def strategy():
    """策略管理 (虚拟盘)"""
    pass


# ─── 资金费率套利 ──────────────────────────────────────────────────


@strategy.command("funding-scan")
@click.option("--json-output", is_flag=True, help="JSON 输出")
def funding_scan(json_output: bool):
    """扫描资金费率套利机会"""
    from cryptobot.strategy.funding_arb import scan_funding_opportunities

    signals = scan_funding_opportunities()

    if json_output:
        from dataclasses import asdict
        click.echo(json.dumps(
            [asdict(s) for s in signals], indent=2, ensure_ascii=False,
        ))
        return

    if not signals:
        console.print("[yellow]未发现套利机会[/yellow]")
        return

    table = Table(title="资金费率套利机会")
    table.add_column("币种", style="cyan")
    table.add_column("费率(8h)", justify="right")
    table.add_column("年化%", justify="right")
    table.add_column("置信度", justify="right")

    for s in signals:
        table.add_row(
            s.symbol,
            f"{s.funding_rate:.4%}",
            f"{s.annualized_rate:.1f}%",
            str(s.confidence),
        )
    console.print(table)


@strategy.command("funding-run")
@click.option("--json-output", is_flag=True, help="JSON 输出")
def funding_run(json_output: bool):
    """执行资金费率套利扫描+执行"""
    from cryptobot.strategy.funding_arb import run_funding_scan

    result = run_funding_scan()

    if json_output:
        click.echo(json.dumps(result, indent=2, ensure_ascii=False))
        return

    if not result.get("enabled"):
        console.print(f"[yellow]{result.get('reason', '未启用')}[/yellow]")
        return

    lines = [
        f"扫描: {result['scanned']} 个机会",
        f"开仓: {result['opened']} 个",
        f"平仓: {result['closed']} 个",
        f"持仓: {result['open_positions']} 个",
        f"余额: ${result['balance']:.2f}",
    ]
    console.print(Panel("\n".join(lines), title="资金费率套利"))


@strategy.command("funding-status")
@click.option("--json-output", is_flag=True, help="JSON 输出")
def funding_status(json_output: bool):
    """查看资金费率套利虚拟盘状态"""
    from cryptobot.strategy.funding_arb import calc_arb_pnl
    from cryptobot.strategy.virtual_portfolio import (
        get_portfolio_summary,
        load_portfolio,
    )

    portfolio = load_portfolio("funding_arb")
    summary = get_portfolio_summary(portfolio)
    pnl_stats = calc_arb_pnl(portfolio)

    result = {**summary, "arb_stats": pnl_stats}

    if json_output:
        click.echo(json.dumps(result, indent=2, ensure_ascii=False))
        return

    lines = [
        f"初始余额: ${summary['initial_balance']:.2f}",
        f"当前余额: ${summary['current_balance']:.2f}",
        f"持仓数: {summary['open_positions']}",
        f"已平仓: {summary['closed_trades']}",
        f"已实现盈亏: ${summary['realized_pnl']:.2f}",
        f"收益率: {summary['total_return_pct']:.2f}%",
    ]
    console.print(Panel("\n".join(lines), title="费率套利虚拟盘"))


@strategy.command("portfolio")
@click.option(
    "--name", "strategy_name", required=True,
    type=click.Choice(["funding_arb", "grid"]),
    help="策略名称",
)
@click.option("--json-output", is_flag=True, help="JSON 输出")
def portfolio_cmd(strategy_name: str, json_output: bool):
    """查看虚拟盘总览"""
    from cryptobot.strategy.virtual_portfolio import (
        get_portfolio_summary,
        load_portfolio,
    )

    portfolio = load_portfolio(strategy_name)
    summary = get_portfolio_summary(portfolio)

    if json_output:
        click.echo(json.dumps(summary, indent=2, ensure_ascii=False))
        return

    lines = [
        f"策略: {strategy_name}",
        f"初始余额: ${summary['initial_balance']:.2f}",
        f"当前余额: ${summary['current_balance']:.2f}",
        f"持仓数: {summary['open_positions']}",
        f"已平仓: {summary['closed_trades']}",
        f"已实现: ${summary['realized_pnl']:.2f}",
        f"收益率: {summary['total_return_pct']:.2f}%",
    ]
    console.print(Panel("\n".join(lines), title=f"虚拟盘 - {strategy_name}"))


# ─── 网格交易 ──────────────────────────────────────────────────────


@strategy.command("grid-create")
@click.option("--symbol", required=True, help="交易对 (e.g. BTCUSDT)")
@click.option("--grids", default=10, help="网格数量")
@click.option("--investment", default=1000.0, help="投资额")
@click.option("--leverage", default=1, help="杠杆")
@click.option("--auto-range", is_flag=True, help="自动检测价格范围")
@click.option("--upper", type=float, help="手动上界")
@click.option("--lower", type=float, help="手动下界")
@click.option("--json-output", is_flag=True, help="JSON 输出")
def grid_create(
    symbol: str, grids: int, investment: float,
    leverage: int, auto_range: bool,
    upper: float | None, lower: float | None,
    json_output: bool,
):
    """创建网格交易"""
    from cryptobot.strategy.grid_trading import (
        GridConfig,
        auto_detect_range,
        calc_grid_metrics,
        create_grid,
        save_grid_state,
    )

    if auto_range or (upper is None and lower is None):
        try:
            lower, upper = auto_detect_range(symbol)
            if not json_output:
                console.print(
                    f"[cyan]自动检测范围: {lower:.2f} - {upper:.2f}[/cyan]"
                )
        except ValueError as e:
            console.print(f"[red]{e}[/red]")
            return
    elif upper is None or lower is None:
        console.print("[red]请同时指定 --upper 和 --lower[/red]")
        return

    config = GridConfig(
        symbol=symbol,
        upper_price=upper,
        lower_price=lower,
        grid_count=grids,
        total_investment=investment,
        leverage=leverage,
    )

    state = create_grid(config)
    save_grid_state(state)

    metrics = calc_grid_metrics(state)
    if json_output:
        click.echo(json.dumps(metrics, indent=2, ensure_ascii=False))
    else:
        console.print(Panel(
            f"币种: {symbol}\n"
            f"范围: {lower:.2f} - {upper:.2f}\n"
            f"网格数: {grids}\n"
            f"投资额: ${investment:.2f}\n"
            f"杠杆: {leverage}x\n"
            f"级别数: {metrics['total_levels']}",
            title="网格已创建",
        ))


@strategy.command("grid-status")
@click.option("--symbol", required=True, help="交易对")
@click.option("--json-output", is_flag=True, help="JSON 输出")
def grid_status(symbol: str, json_output: bool):
    """查看网格状态"""
    from cryptobot.strategy.grid_trading import calc_grid_metrics, load_grid_state

    state = load_grid_state(symbol)
    if state is None:
        console.print(f"[yellow]未找到 {symbol} 的网格[/yellow]")
        return

    metrics = calc_grid_metrics(state)

    if json_output:
        click.echo(json.dumps(metrics, indent=2, ensure_ascii=False))
        return

    lines = [
        f"币种: {metrics['symbol']}",
        f"范围: {metrics['lower_price']:.2f} - {metrics['upper_price']:.2f}",
        f"网格数: {metrics['grid_count']}",
        f"已触发: {metrics['filled_levels']}/{metrics['total_levels']}",
        f"已实现盈亏: ${metrics['realized_pnl']:.2f}",
        f"创建时间: {metrics['created_at'][:16]}",
    ]
    console.print(Panel("\n".join(lines), title=f"网格 - {symbol}"))


@strategy.command("grid-check")
@click.option("--symbol", required=True, help="交易对")
@click.option("--json-output", is_flag=True, help="JSON 输出")
def grid_check(symbol: str, json_output: bool):
    """检查网格触发"""
    from cryptobot.strategy.grid_trading import run_grid_check

    result = run_grid_check(symbol)

    if json_output:
        click.echo(json.dumps(result, indent=2, ensure_ascii=False))
        return

    if "error" in result:
        console.print(f"[red]{result['error']}[/red]")
        return

    console.print(Panel(
        f"币种: {result['symbol']}\n"
        f"当前价: {result.get('current_price', 0):.2f}\n"
        f"已触发: {result['filled_levels']}/{result['total_levels']}\n"
        f"已实现盈亏: ${result['realized_pnl']:.2f}",
        title="网格检查",
    ))
