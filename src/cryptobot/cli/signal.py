"""信号命令: Agent → Freqtrade 信号桥接"""

import json
from datetime import datetime, timezone, timedelta
from pathlib import Path

import click
from rich.console import Console
from rich.table import Table

console = Console()

SIGNAL_DIR = Path("data/output/signals")
SIGNAL_FILE = SIGNAL_DIR / "signal.json"


def _load_signals() -> dict:
    if not SIGNAL_FILE.exists():
        return {"signals": [], "last_updated": None}
    return json.loads(SIGNAL_FILE.read_text())


def _save_signals(data: dict):
    SIGNAL_DIR.mkdir(parents=True, exist_ok=True)
    tmp = SIGNAL_FILE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False))
    tmp.rename(SIGNAL_FILE)  # 原子写入


@click.group()
def signal():
    """交易信号管理"""
    pass


@signal.command()
@click.option("--symbol", required=True, help="交易对 (如 BTCUSDT)")
@click.option(
    "--action",
    required=True,
    type=click.Choice(["long", "short", "close_long", "close_short"]),
    help="交易方向",
)
@click.option("--leverage", default=3, type=int, help="杠杆倍数")
@click.option("--amount", default=1000, type=float, help="仓位大小 (USDT)")
@click.option("--sl", type=float, help="止损价")
@click.option("--tp", type=float, help="止盈价")
@click.option("--confidence", default=50, type=int, help="信心度 (0-100)")
@click.option("--expiry-hours", default=4, type=int, help="信号有效期 (小时)")
def write(
    symbol: str,
    action: str,
    leverage: int,
    amount: float,
    sl: float | None,
    tp: float | None,
    confidence: int,
    expiry_hours: int,
):
    """写入交易信号"""
    now = datetime.now(timezone.utc)
    new_signal = {
        "symbol": symbol,
        "timestamp": now.isoformat(),
        "action": action,
        "leverage": leverage,
        "position_size_usdt": amount,
        "stop_loss": sl,
        "take_profit": [{"price": tp, "close_pct": 100}] if tp else [],
        "confidence": confidence,
        "expires_at": (now + timedelta(hours=expiry_hours)).isoformat(),
    }

    data = _load_signals()
    # 替换同交易对的旧信号
    data["signals"] = [s for s in data["signals"] if s["symbol"] != symbol]
    data["signals"].append(new_signal)
    data["last_updated"] = now.isoformat()
    _save_signals(data)

    console.print(f"[green]信号已写入[/green]: {symbol} {action} {leverage}x ${amount}")
    if sl:
        console.print(f"  止损: {sl}")
    if tp:
        console.print(f"  止盈: {tp}")


@signal.command()
@click.option("--json-output", is_flag=True, help="输出 JSON 格式")
def show(json_output: bool):
    """查看当前信号"""
    data = _load_signals()
    if not data["signals"]:
        if json_output:
            click.echo(json.dumps({"signals": [], "last_updated": None}, ensure_ascii=False))
        else:
            console.print("[yellow]当前无活跃信号[/yellow]")
        return

    if json_output:
        click.echo(json.dumps(data, indent=2, ensure_ascii=False))
        return

    table = Table(title="当前交易信号")
    table.add_column("交易对", style="cyan")
    table.add_column("方向", style="bold")
    table.add_column("杠杆")
    table.add_column("金额")
    table.add_column("止损")
    table.add_column("止盈")
    table.add_column("信心度")
    table.add_column("过期时间")

    now = datetime.now(timezone.utc)
    for s in data["signals"]:
        expires = datetime.fromisoformat(s["expires_at"])
        expired = expires < now
        style = "dim" if expired else ""

        direction_color = "green" if s["action"] in ("long",) else "red"
        tp_str = str(s["take_profit"][0]["price"]) if s.get("take_profit") else "-"

        table.add_row(
            s["symbol"],
            f"[{direction_color}]{s['action']}[/{direction_color}]",
            str(s.get("leverage", "-")),
            f"${s.get('position_size_usdt', '-')}",
            str(s.get("stop_loss", "-")),
            tp_str,
            f"{s.get('confidence', '-')}%",
            ("已过期" if expired else expires.strftime("%m-%d %H:%M")),
            style=style,
        )

    console.print(table)


@signal.command()
@click.option("--symbol", default=None, help="指定交易对 (不指定则清除所有)")
def clear(symbol: str | None):
    """清除已执行/过期的信号"""
    data = _load_signals()
    before = len(data["signals"])

    if symbol:
        data["signals"] = [s for s in data["signals"] if s["symbol"] != symbol]
    else:
        now = datetime.now(timezone.utc)
        data["signals"] = [
            s for s in data["signals"] if datetime.fromisoformat(s["expires_at"]) > now
        ]

    after = len(data["signals"])
    data["last_updated"] = datetime.now(timezone.utc).isoformat()
    _save_signals(data)
    console.print(f"[green]已清除 {before - after} 条信号[/green]，剩余 {after} 条")
