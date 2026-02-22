"""CLI: 特征工程命令"""

import json

import click
from rich.console import Console
from rich.table import Table

console = Console()


@click.group()
def features():
    """特征工程"""
    pass


@features.command("factor-analysis")
@click.option("--days", default=90, help="分析天数")
@click.option("--json-output", is_flag=True, help="JSON 输出")
def factor_analysis(days: int, json_output: bool):
    """多因子相关性分析"""
    from cryptobot.features.factor_analysis import run_factor_analysis

    result = run_factor_analysis(days=days)

    if json_output:
        data = {
            "total_factors": len(set(f.factor_name for f in result.factors)),
            "significant_count": len(result.top_predictors),
            "optimal_lags": result.optimal_lags,
            "top_predictors": [
                {
                    "factor_name": f.factor_name,
                    "lag_hours": f.lag_hours,
                    "correlation": f.correlation,
                    "p_value": f.p_value,
                    "sample_size": f.sample_size,
                }
                for f in result.top_predictors[:20]
            ],
        }
        click.echo(json.dumps(data, indent=2, ensure_ascii=False))
        return

    if not result.factors:
        console.print(f"[yellow]近 {days} 天无可用特征数据[/yellow]")
        return

    console.print(result.report)

    # Rich 表格展示 top predictors
    if result.top_predictors:
        table = Table(title="\nTop 预测因子")
        table.add_column("因子", style="cyan")
        table.add_column("Lag(h)", justify="right")
        table.add_column("r", justify="right")
        table.add_column("p-value", justify="right")
        table.add_column("N", justify="right")
        for fc in result.top_predictors[:10]:
            table.add_row(
                fc.factor_name,
                str(fc.lag_hours),
                f"{fc.correlation:.4f}",
                f"{fc.p_value:.4f}",
                str(fc.sample_size),
            )
        console.print(table)
