"""指标命令: 技术指标计算"""

import json

import click
from rich.console import Console
from rich.panel import Panel

console = Console()


@click.group()
def indicator():
    """技术指标计算"""
    pass


@indicator.command("all")
@click.option("--symbol", required=True, help="交易对 (如 BTCUSDT)")
@click.option("--interval", default="4h", help="K线周期 (如 5m, 1h, 4h, 1d)")
@click.option("--json-output", is_flag=True, help="输出 JSON 格式")
def all_indicators(symbol: str, interval: str, json_output: bool):
    """计算全部技术指标"""
    from cryptobot.indicators.calculator import calc_all_indicators

    try:
        result = calc_all_indicators(symbol, interval)
    except FileNotFoundError as e:
        console.print(f"[red]{e}[/red]")
        raise SystemExit(1)

    if json_output:
        console.print_json(json.dumps(result, default=str))
        return

    # 美化输出
    t = result["trend"]
    m = result["momentum"]
    v = result["volatility"]
    s = result["signals"]

    score = s["technical_score"]
    sc = "green" if score > 0 else "red" if score < 0 else "white"

    console.print(Panel(
        f"[bold]收盘价:[/bold] {result['latest_close']:,.2f}  |  "
        f"[bold]时间:[/bold] {result['latest_time']}  |  "
        f"K线数: {result['kline_count']}",
        title=f"{symbol} 技术分析 ({interval})",
    ))

    # 趋势
    ea = t["ema_alignment"]
    ea_color = "green" if ea == "bullish" else "red" if ea == "bearish" else "yellow"
    trend_text = (
        f"EMA 7/25/99: {_f(t['ema_7'])} / {_f(t['ema_25'])} / {_f(t['ema_99'])}  "
        f"[{ea_color}]{ea}[/{ea_color}]\n"
        f"MACD: {_f(t['macd'])}  Signal: {_f(t['macd_signal'])}  "
        f"Hist: {_f(t['macd_hist'])}  {t['macd_cross']}\n"
        f"ADX: {_f(t['adx'])}  DI+: {_f(t['di_plus'])}  DI-: {_f(t['di_minus'])}"
    )
    console.print(Panel(trend_text, title="趋势"))

    # 动量
    rsi = m["rsi_14"]
    rsi_c = "red" if m["rsi_zone"] == "overbought" else "green" if m["rsi_zone"] == "oversold" else "white"
    mom_text = (
        f"RSI(14): [{rsi_c}]{_f(rsi)} ({m['rsi_zone']})[/{rsi_c}]\n"
        f"StochRSI K/D: {_f(m['stochrsi_k'])} / {_f(m['stochrsi_d'])}\n"
        f"CCI: {_f(m['cci_20'])}  |  Williams %R: {_f(m['willr_14'])}  |  MFI: {_f(m['mfi_14'])}"
    )
    console.print(Panel(mom_text, title="动量"))

    # 波动率
    vol_text = (
        f"Bollinger: {_f(v['bb_lower'])} / {_f(v['bb_middle'])} / {_f(v['bb_upper'])}\n"
        f"BB宽度: {_f(v['bb_width'])}%  |  位置: {_f(v['bb_position'])} (0=下轨, 1=上轨)\n"
        f"ATR(14): {_f(v['atr_14'])}  ({_f(v['atr_pct'])}%)"
    )
    console.print(Panel(vol_text, title="波动率"))

    # 综合信号
    signals_str = ", ".join(s["signals"]) if s["signals"] else "无明显信号"
    console.print(Panel(
        f"[bold]评分:[/bold] [{sc}]{score:+.1f}[/{sc}] / 10\n"
        f"[bold]倾向:[/bold] [{sc}]{s['bias']}[/{sc}]\n"
        f"[bold]信号:[/bold] {signals_str}",
        title="综合判断",
    ))


@indicator.command("crypto")
@click.option("--symbol", required=True, help="交易对 (如 BTCUSDT)")
@click.option("--json-output", is_flag=True, help="输出 JSON 格式")
def crypto_indicators(symbol: str, json_output: bool):
    """加密货币特有指标 (资金费率/OI/多空比)"""
    from cryptobot.indicators.crypto_specific import calc_crypto_indicators

    try:
        result = calc_crypto_indicators(symbol)
    except Exception as e:
        console.print(f"[red]获取失败: {e}[/red]")
        raise SystemExit(1)

    if json_output:
        console.print_json(json.dumps(result, default=str))
        return

    f = result["funding"]
    oi = result["open_interest"]
    tk = result["taker_ratio"]
    ls = result["long_short"]
    comp = result["composite"]

    # 资金费率
    fc = "green" if f["current_rate_pct"] >= 0 else "red"
    console.print(Panel(
        f"[bold]当前费率:[/bold] [{fc}]{f['current_rate_pct']:.4f}%[/{fc}]\n"
        f"[bold]均值:[/bold] {f['avg_rate_pct']:.4f}%\n"
        f"[bold]信号:[/bold] {f['signal']}",
        title=f"{symbol} 资金费率",
    ))

    # OI
    oc = "green" if oi["change_pct"] >= 0 else "red"
    console.print(Panel(
        f"[bold]OI:[/bold] ${oi['current_oi_value']:,.0f}\n"
        f"[bold]变化:[/bold] [{oc}]{oi['change_pct']:+.2f}%[/{oc}]\n"
        f"[bold]信号:[/bold] {oi['signal']}",
        title="持仓量",
    ))

    # 主动买卖比
    tc = "green" if tk["current_ratio"] > 1 else "red"
    console.print(Panel(
        f"[bold]买卖比:[/bold] [{tc}]{tk['current_ratio']:.4f}[/{tc}]\n"
        f"[bold]信号:[/bold] {tk['signal']}",
        title="主动买卖比",
    ))

    # 多空比
    console.print(Panel(
        f"[bold]全网多空:[/bold] {ls['global_long_pct']:.1f}% / {ls['global_short_pct']:.1f}%\n"
        f"[bold]大户多空:[/bold] {ls['top_trader_long_pct']:.1f}% 做多\n"
        f"[bold]分歧:[/bold] {ls['divergence_signal']}",
        title="多空比",
    ))

    # 综合
    sc = "green" if comp["score"] > 0 else "red" if comp["score"] < 0 else "white"
    signals_str = ", ".join(comp["signals"]) if comp["signals"] else "无明显信号"
    console.print(Panel(
        f"[bold]评分:[/bold] [{sc}]{comp['score']:+.1f}[/{sc}] / 10\n"
        f"[bold]倾向:[/bold] [{sc}]{comp['bias']}[/{sc}]\n"
        f"[bold]信号:[/bold] {signals_str}",
        title="链上综合判断",
    ))


def _f(val) -> str:
    """格式化数值"""
    if val is None:
        return "-"
    if isinstance(val, float):
        if abs(val) > 1000:
            return f"{val:,.2f}"
        return f"{val:.4f}"
    return str(val)
