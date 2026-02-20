"""CLI: 事件驱动监控命令"""

import click
from rich.console import Console

console = Console()


@click.group()
def events():
    """价格异动事件监控"""
    pass


@events.command("start")
@click.option("--verbose", "-v", is_flag=True, help="详细日志")
def start(verbose: bool):
    """启动价格异动监控 (阻塞运行)"""
    import logging

    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )

    from cryptobot.config import load_settings

    settings = load_settings()
    event_cfg = settings.get("events", {})

    console.print("[cyan]价格异动监控启动[/cyan]")
    console.print(f"  轮询间隔: {event_cfg.get('poll_interval_seconds', 30)}s")
    console.print(f"  5min 阈值: {event_cfg.get('threshold_5min_pct', 3.0)}%")
    console.print(f"  15min 阈值: {event_cfg.get('threshold_15min_pct', 5.0)}%")
    console.print(f"  冷却时间: {event_cfg.get('cooldown_minutes', 30)}min")
    console.print("\n按 Ctrl+C 停止\n")

    from cryptobot.events.price_monitor import run_price_monitor

    try:
        run_price_monitor()
    except KeyboardInterrupt:
        console.print("\n[yellow]监控已停止[/yellow]")


@events.command("status")
@click.option("--json-output", is_flag=True, help="JSON 输出")
def status(json_output: bool):
    """查看事件监控配置"""
    import json

    from cryptobot.config import load_settings, get_all_symbols

    settings = load_settings()
    event_cfg = settings.get("events", {})
    symbols = get_all_symbols()

    info = {
        "symbols": symbols,
        "poll_interval_seconds": event_cfg.get("poll_interval_seconds", 30),
        "threshold_5min_pct": event_cfg.get("threshold_5min_pct", 3.0),
        "threshold_15min_pct": event_cfg.get("threshold_15min_pct", 5.0),
        "cooldown_minutes": event_cfg.get("cooldown_minutes", 30),
    }

    if json_output:
        click.echo(json.dumps(info, indent=2))
    else:
        console.print("[bold]事件监控配置[/bold]")
        console.print(f"  监控币种: {len(symbols)} 个")
        console.print(f"  轮询间隔: {info['poll_interval_seconds']}s")
        console.print(f"  5min 阈值: {info['threshold_5min_pct']}%")
        console.print(f"  15min 阈值: {info['threshold_15min_pct']}%")
        console.print(f"  冷却时间: {info['cooldown_minutes']}min")
