"""数据命令: 链上/情绪/新闻数据获取"""

import json

import click
from rich.console import Console
from rich.table import Table
from rich.panel import Panel

console = Console()


@click.group()
def data():
    """数据获取命令"""
    pass


# ─── 链上数据 ───────────────────────────────────────────

@data.command()
@click.option("--symbol", required=True, help="交易对 (如 BTCUSDT)")
@click.option(
    "--type",
    "data_type",
    required=True,
    type=click.Choice(["funding-rate", "open-interest", "taker-ratio"]),
    help="链上数据类型",
)
@click.option("--limit", default=30, help="数据条数")
@click.option("--json-output", is_flag=True, help="输出 JSON 格式")
def onchain(symbol: str, data_type: str, limit: int, json_output: bool):
    """链上数据 (Binance 公开 API)"""
    from cryptobot.data.onchain import (
        get_funding_rate,
        get_open_interest_hist,
        get_taker_buy_sell_ratio,
    )

    try:
        if data_type == "funding-rate":
            result = get_funding_rate(symbol, limit=limit)
            if json_output:
                console.print_json(json.dumps(result, default=str))
                return
            _print_funding_rate(result)

        elif data_type == "open-interest":
            result = get_open_interest_hist(symbol, limit=limit)
            if json_output:
                console.print_json(json.dumps(result, default=str))
                return
            _print_open_interest(result)

        elif data_type == "taker-ratio":
            result = get_taker_buy_sell_ratio(symbol, limit=limit)
            if json_output:
                console.print_json(json.dumps(result, default=str))
                return
            _print_taker_ratio(result)

    except Exception as e:
        console.print(f"[red]获取失败: {e}[/red]")
        raise SystemExit(1)


def _print_funding_rate(data: dict):
    rate = data["current_rate"]
    color = "green" if rate >= 0 else "red"
    console.print(Panel(
        f"[bold]当前费率:[/bold] [{color}]{rate*100:.4f}%[/{color}]\n"
        f"[bold]30期均值:[/bold] {data['avg_rate_30']*100:.4f}%\n"
        f"[bold]30期最高:[/bold] {data['max_rate_30']*100:.4f}%\n"
        f"[bold]30期最低:[/bold] {data['min_rate_30']*100:.4f}%\n"
        f"[bold]正/负次数:[/bold] {data['positive_count']}/{data['negative_count']}",
        title=f"{data['symbol']} 资金费率",
    ))


def _print_open_interest(data: dict):
    change = data["oi_change_pct"]
    color = "green" if change >= 0 else "red"
    console.print(Panel(
        f"[bold]当前 OI:[/bold] ${data['current_oi_value']:,.0f}\n"
        f"[bold]变化:[/bold] [{color}]{change:+.2f}%[/{color}]\n"
        f"[bold]最高:[/bold] ${data['max_oi_value']:,.0f}\n"
        f"[bold]最低:[/bold] ${data['min_oi_value']:,.0f}",
        title=f"{data['symbol']} 持仓量 ({data['period']})",
    ))


def _print_taker_ratio(data: dict):
    ratio = data["current_ratio"]
    color = "green" if ratio > 1 else "red" if ratio < 1 else "white"
    console.print(Panel(
        f"[bold]当前比率:[/bold] [{color}]{ratio:.4f}[/{color}]  (>1 买方强势)\n"
        f"[bold]均值:[/bold] {data['avg_ratio']:.4f}\n"
        f"[bold]多/空期数:[/bold] {data['bullish_count']}/{data['bearish_count']}",
        title=f"{data['symbol']} 主动买卖比 ({data['period']})",
    ))


# ─── 情绪数据 ───────────────────────────────────────────

@data.command()
@click.option(
    "--type",
    "data_type",
    required=True,
    type=click.Choice(["fear-greed", "long-short-ratio", "top-trader-ratio"]),
    help="情绪数据类型",
)
@click.option("--symbol", default=None, help="交易对 (多空比需要)")
@click.option("--json-output", is_flag=True, help="输出 JSON 格式")
def sentiment(data_type: str, symbol: str | None, json_output: bool):
    """情绪数据 (Fear & Greed / 多空比)"""
    from cryptobot.data.sentiment import (
        get_fear_greed_index,
        get_long_short_ratio,
        get_top_trader_long_short,
    )

    try:
        if data_type == "fear-greed":
            result = get_fear_greed_index()
            if json_output:
                console.print_json(json.dumps(result, default=str))
                return
            _print_fear_greed(result)

        elif data_type == "long-short-ratio":
            if not symbol:
                console.print("[red]多空比需要指定 --symbol[/red]")
                raise SystemExit(1)
            result = get_long_short_ratio(symbol)
            if json_output:
                console.print_json(json.dumps(result, default=str))
                return
            _print_long_short(result)

        elif data_type == "top-trader-ratio":
            if not symbol:
                console.print("[red]大户多空比需要指定 --symbol[/red]")
                raise SystemExit(1)
            result = get_top_trader_long_short(symbol)
            if json_output:
                console.print_json(json.dumps(result, default=str))
                return
            _print_top_trader(result)

    except Exception as e:
        console.print(f"[red]获取失败: {e}[/red]")
        raise SystemExit(1)


def _print_fear_greed(data: dict):
    val = data["current_value"]
    if val <= 25:
        color, emoji = "red", "极度恐惧"
    elif val <= 40:
        color, emoji = "yellow", "恐惧"
    elif val <= 60:
        color, emoji = "white", "中性"
    elif val <= 75:
        color, emoji = "green", "贪婪"
    else:
        color, emoji = "bright_green", "极度贪婪"

    console.print(Panel(
        f"[bold]当前:[/bold] [{color}]{val} ({emoji})[/{color}]\n"
        f"[bold]分类:[/bold] {data['current_classification']}\n"
        f"[bold]7日均值:[/bold] {data['avg_7d']:.0f}\n"
        f"[bold]30日均值:[/bold] {data['avg_30d']:.0f}\n"
        f"[bold]趋势:[/bold] {data['trend']}",
        title="恐惧贪婪指数",
    ))


def _print_long_short(data: dict):
    ratio = data["current_ratio"]
    color = "green" if ratio > 1 else "red"
    console.print(Panel(
        f"[bold]多空比:[/bold] [{color}]{ratio:.4f}[/{color}]\n"
        f"[bold]做多:[/bold] {data['current_long_pct']:.1f}%\n"
        f"[bold]做空:[/bold] {data['current_short_pct']:.1f}%\n"
        f"[bold]均值:[/bold] {data['avg_ratio']:.4f}\n"
        f"[bold]极值:[/bold] {data['min_ratio']:.4f} ~ {data['max_ratio']:.4f}",
        title=f"{data['symbol']} 全网多空比 ({data['period']})",
    ))


def _print_top_trader(data: dict):
    ratio = data["current_ratio"]
    color = "green" if ratio > 1 else "red"
    console.print(Panel(
        f"[bold]大户多空比:[/bold] [{color}]{ratio:.4f}[/{color}]\n"
        f"[bold]大户做多:[/bold] {data['current_long_pct']:.1f}%\n"
        f"[bold]大户做空:[/bold] {data['current_short_pct']:.1f}%\n"
        f"[bold]均值:[/bold] {data['avg_ratio']:.4f}",
        title=f"{data['symbol']} 大户多空比 ({data['period']})",
    ))


# ─── 新闻/市场概览 ──────────────────────────────────────

@data.command()
@click.option("--symbol", default=None, help="币种 (如 BTC)")
@click.option("--type", "data_type", default="overview",
              type=click.Choice(["overview", "coin", "trending"]))
@click.option("--json-output", is_flag=True, help="输出 JSON 格式")
def news(symbol: str | None, data_type: str, json_output: bool):
    """市场概览与新闻 (CoinGecko)"""
    from cryptobot.data.news import get_market_overview, get_coin_info, get_trending

    try:
        if data_type == "overview":
            result = get_market_overview()
            if json_output:
                console.print_json(json.dumps(result, default=str))
                return
            console.print(Panel(
                f"[bold]总市值:[/bold] ${result['total_market_cap_usd']:,.0f}\n"
                f"[bold]24h 成交量:[/bold] ${result['total_volume_24h_usd']:,.0f}\n"
                f"[bold]BTC 占比:[/bold] {result['btc_dominance']:.1f}%\n"
                f"[bold]ETH 占比:[/bold] {result['eth_dominance']:.1f}%\n"
                f"[bold]24h 市值变化:[/bold] {result['market_cap_change_24h_pct']:.2f}%",
                title="加密货币市场概览",
            ))

        elif data_type == "coin":
            if not symbol:
                console.print("[red]请指定 --symbol[/red]")
                raise SystemExit(1)
            result = get_coin_info(symbol)
            if "error" in result:
                console.print(f"[red]{result['error']}[/red]")
                raise SystemExit(1)
            if json_output:
                console.print_json(json.dumps(result, default=str))
                return
            chg24 = result['price_change_24h_pct']
            c24 = "green" if chg24 >= 0 else "red"
            console.print(Panel(
                f"[bold]价格:[/bold] ${result['current_price']:,.2f}\n"
                f"[bold]24h:[/bold] [{c24}]{chg24:+.2f}%[/{c24}]\n"
                f"[bold]7d:[/bold] {result['price_change_7d_pct']:+.2f}%\n"
                f"[bold]30d:[/bold] {result['price_change_30d_pct']:+.2f}%\n"
                f"[bold]市值排名:[/bold] #{result['market_cap_rank']}\n"
                f"[bold]ATH:[/bold] ${result['ath']:,.2f} ({result['ath_change_pct']:.1f}%)\n"
                f"[bold]社区情绪:[/bold] {result['sentiment_up_pct']:.0f}% 看涨",
                title=f"{result['name']} ({result['symbol']})",
            ))

        elif data_type == "trending":
            result = get_trending()
            if json_output:
                console.print_json(json.dumps(result, default=str))
                return
            table = Table(title="热门趋势币种")
            table.add_column("#")
            table.add_column("名称")
            table.add_column("符号")
            table.add_column("市值排名")
            for i, c in enumerate(result["trending_coins"], 1):
                table.add_row(str(i), c["name"], c["symbol"], str(c["market_cap_rank"]))
            console.print(table)

    except Exception as e:
        console.print(f"[red]获取失败: {e}[/red]")
        raise SystemExit(1)
