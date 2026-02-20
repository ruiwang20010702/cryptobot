"""监控命令: 告警检查 / 爆仓距离 / 日报"""

import json
from datetime import datetime, timezone

import click
from rich.console import Console
from rich.table import Table
from rich.panel import Panel

from cryptobot.freqtrade_api import ft_api_get
from cryptobot.risk.liquidation_calc import (
    calc_liquidation_price,
    calc_liquidation_distance,
    assess_liquidation_risk,
)
from cryptobot.signal.bridge import read_signals

console = Console()


def _build_position_alerts(positions: list[dict], signals: list[dict]) -> list[dict]:
    """基于持仓和信号数据构建告警列表"""
    alerts = []
    now = datetime.now(timezone.utc)

    # 建立信号查找表
    signal_map = {}
    for s in signals:
        signal_map[s["symbol"]] = s

    for pos in positions:
        pair = pos.get("pair", "")
        symbol = pair.replace("/", "").replace(":USDT", "")
        signal = signal_map.get(symbol, {})

        current_price = pos.get("current_rate", 0)
        entry_price = pos.get("open_rate", 0)
        is_short = pos.get("is_short", False)
        side = "short" if is_short else "long"
        leverage = pos.get("leverage", 1)

        if not current_price or not entry_price:
            continue

        # 1. 爆仓距离告警
        liq_price = calc_liquidation_price(entry_price, leverage, side)
        liq_dist = calc_liquidation_distance(current_price, liq_price)
        risk = assess_liquidation_risk(liq_dist)

        if risk["level"] in ("critical", "danger", "warning"):
            alerts.append({
                "time": now.isoformat(),
                "level": "CRITICAL" if risk["level"] in ("critical", "danger") else "WARNING",
                "type": "liquidation_distance",
                "symbol": symbol,
                "message": f"{symbol} 爆仓距离 {liq_dist:.1f}%，风险等级: {risk['level']}",
                "current_price": current_price,
                "liquidation_price": liq_price,
                "distance_pct": liq_dist,
                "action": risk["action"],
            })

        # 2. 止损告警
        sl = signal.get("stop_loss") or pos.get("stop_loss_abs")
        if sl and current_price:
            if side == "long":
                sl_dist = (current_price - sl) / current_price * 100
            else:
                sl_dist = (sl - current_price) / current_price * 100

            if sl_dist <= 0:
                alerts.append({
                    "time": now.isoformat(),
                    "level": "CRITICAL",
                    "type": "stop_loss_hit",
                    "symbol": symbol,
                    "message": f"{symbol} 已触及止损! 当前 {current_price:.2f}，止损 {sl:.2f}",
                    "current_price": current_price,
                    "stop_loss": sl,
                    "action": "Freqtrade 应自动执行止损",
                })
            elif sl_dist < 1:
                alerts.append({
                    "time": now.isoformat(),
                    "level": "WARNING",
                    "type": "approaching_stop_loss",
                    "symbol": symbol,
                    "message": f"{symbol} 接近止损，当前 {current_price:.2f}，距止损 {sl_dist:.2f}%",
                    "current_price": current_price,
                    "stop_loss": sl,
                    "distance_pct": round(sl_dist, 2),
                    "action": "密切关注",
                })

        # 3. 止盈告警
        tp_list = signal.get("take_profit", [])
        if tp_list and current_price:
            for tp_item in tp_list:
                tp_price = tp_item.get("price")
                if not tp_price:
                    continue
                if side == "long" and current_price >= tp_price:
                    pnl_pct = (current_price - entry_price) / entry_price * 100 * leverage
                    alerts.append({
                        "time": now.isoformat(),
                        "level": "IMPORTANT",
                        "type": "take_profit_hit",
                        "symbol": symbol,
                        "message": f"{symbol} 达到止盈目标 {tp_price}! 盈利 {pnl_pct:.1f}%",
                        "current_price": current_price,
                        "take_profit": tp_price,
                        "action": "考虑部分平仓",
                    })
                elif side == "short" and current_price <= tp_price:
                    pnl_pct = (entry_price - current_price) / entry_price * 100 * leverage
                    alerts.append({
                        "time": now.isoformat(),
                        "level": "IMPORTANT",
                        "type": "take_profit_hit",
                        "symbol": symbol,
                        "message": f"{symbol} 达到止盈目标 {tp_price}! 盈利 {pnl_pct:.1f}%",
                        "current_price": current_price,
                        "take_profit": tp_price,
                        "action": "考虑部分平仓",
                    })

    return alerts


def _build_signal_only_alerts(signals: list[dict]) -> list[dict]:
    """仅基于信号数据构建告警 (Freqtrade 未运行时)"""
    alerts = []
    now = datetime.now(timezone.utc)

    for s in signals:
        symbol = s["symbol"]
        side = s.get("action", "long")
        if side not in ("long", "short"):
            continue

        # 信号过期检查
        expires = datetime.fromisoformat(s["expires_at"])
        if expires < now:
            alerts.append({
                "time": now.isoformat(),
                "level": "INFO",
                "type": "signal_expired",
                "symbol": symbol,
                "message": f"{symbol} 信号已过期",
                "action": "等待下一分析周期",
            })

    return alerts


@click.group()
def monitor():
    """持仓监控与告警"""
    pass


@monitor.command("check-alerts")
@click.option("--json-output", is_flag=True, help="输出 JSON 格式")
def check_alerts(json_output: bool):
    """检查告警条件 (爆仓距离/止损/止盈/信号过期)"""
    now = datetime.now(timezone.utc)
    signals = read_signals(filter_expired=False)

    # 尝试从 Freqtrade 获取持仓
    positions = ft_api_get("/status")

    if positions:
        alerts = _build_position_alerts(positions, signals)
    else:
        alerts = _build_signal_only_alerts(signals)

    result = {
        "check_time": now.isoformat(),
        "freqtrade_connected": positions is not None,
        "active_positions": len(positions) if positions else 0,
        "active_signals": len([s for s in signals if datetime.fromisoformat(s["expires_at"]) > now]),
        "alerts": alerts,
        "alert_count": {
            "CRITICAL": len([a for a in alerts if a["level"] == "CRITICAL"]),
            "WARNING": len([a for a in alerts if a["level"] == "WARNING"]),
            "IMPORTANT": len([a for a in alerts if a["level"] == "IMPORTANT"]),
            "INFO": len([a for a in alerts if a["level"] == "INFO"]),
        },
    }

    if json_output:
        click.echo(json.dumps(result, indent=2, ensure_ascii=False))
        return

    # Rich 输出
    if not positions:
        console.print("[yellow]Freqtrade 未运行，仅检查信号状态[/yellow]")

    if not alerts:
        console.print("[green]一切正常，无告警[/green]")
        console.print(f"持仓: {result['active_positions']} | 信号: {result['active_signals']}")
        return

    for alert in alerts:
        level = alert["level"]
        color = {"CRITICAL": "red", "WARNING": "yellow", "IMPORTANT": "cyan", "INFO": "dim"}.get(level, "white")
        console.print(f"[{color}][{level}][/{color}] {alert['message']}")
        if alert.get("action"):
            console.print(f"  建议: {alert['action']}")


@monitor.command("liquidation-distance")
@click.option("--json-output", is_flag=True, help="输出 JSON 格式")
def liquidation_distance(json_output: bool):
    """计算各持仓爆仓距离"""
    # 优先从 Freqtrade 获取真实持仓
    positions = ft_api_get("/status")
    signals = read_signals()

    results = []

    if positions:
        for pos in positions:
            pair = pos.get("pair", "")
            symbol = pair.replace("/", "").replace(":USDT", "")
            entry = pos.get("open_rate", 0)
            current = pos.get("current_rate", 0)
            leverage = pos.get("leverage", 1)
            side = "short" if pos.get("is_short", False) else "long"

            if not entry or not current:
                continue

            liq_price = calc_liquidation_price(entry, leverage, side)
            liq_dist = calc_liquidation_distance(current, liq_price)
            risk = assess_liquidation_risk(liq_dist)

            if side == "long":
                pnl_pct = (current - entry) / entry * 100 * leverage
            else:
                pnl_pct = (entry - current) / entry * 100 * leverage

            results.append({
                "symbol": symbol,
                "side": side,
                "leverage": leverage,
                "entry_price": entry,
                "current_price": current,
                "liquidation_price": liq_price,
                "distance_pct": liq_dist,
                "risk_level": risk["level"],
                "pnl_pct": round(pnl_pct, 2),
            })
    elif signals:
        console.print("[yellow]Freqtrade 未运行，基于信号数据估算[/yellow]") if not json_output else None
        for s in signals:
            side = s.get("action", "long")
            if side not in ("long", "short"):
                continue
            leverage = s.get("leverage", 3)
            # 信号中没有当前价，用入场价范围的中值估算
            entry_range = s.get("entry_price_range")
            if entry_range and entry_range[0]:
                entry = (entry_range[0] + entry_range[1]) / 2
            else:
                continue

            liq_price = calc_liquidation_price(entry, leverage, side)
            liq_dist = calc_liquidation_distance(entry, liq_price)  # 用入场价代替当前价
            risk = assess_liquidation_risk(liq_dist)

            results.append({
                "symbol": s["symbol"],
                "side": side,
                "leverage": leverage,
                "entry_price": entry,
                "current_price": None,
                "liquidation_price": liq_price,
                "distance_pct": liq_dist,
                "risk_level": risk["level"],
                "pnl_pct": 0,
                "note": "估算值 (无实时价格)",
            })

    if json_output:
        click.echo(json.dumps(results, indent=2, ensure_ascii=False))
        return

    if not results:
        console.print("[yellow]当前无持仓/信号[/yellow]")
        return

    table = Table(title="爆仓距离分析")
    table.add_column("交易对", style="cyan")
    table.add_column("方向")
    table.add_column("杠杆")
    table.add_column("入场价")
    table.add_column("当前价")
    table.add_column("强平价", style="red")
    table.add_column("爆仓距离", justify="right")
    table.add_column("风险等级")
    table.add_column("盈亏%", justify="right")

    for r in results:
        dir_color = "green" if r["side"] == "long" else "red"
        risk_color = {
            "safe": "green", "caution": "yellow", "warning": "bright_yellow",
            "danger": "red", "critical": "bright_red",
        }.get(r["risk_level"], "white")
        pnl_color = "green" if r["pnl_pct"] >= 0 else "red"

        table.add_row(
            r["symbol"],
            f"[{dir_color}]{r['side'].upper()}[/{dir_color}]",
            f"{r['leverage']}x",
            f"{r['entry_price']:.2f}",
            f"{r['current_price']:.2f}" if r.get("current_price") else "-",
            f"{r['liquidation_price']:.2f}",
            f"[{risk_color}]{r['distance_pct']:.1f}%[/{risk_color}]",
            f"[{risk_color}]{r['risk_level']}[/{risk_color}]",
            f"[{pnl_color}]{r['pnl_pct']:+.2f}%[/{pnl_color}]",
        )

    console.print(table)


@monitor.command("daily-report")
@click.option("--json-output", is_flag=True, help="输出 JSON 格式")
def daily_report(json_output: bool):
    """生成每日报告"""
    now = datetime.now(timezone.utc)
    signals = read_signals(filter_expired=False)
    active_signals = [s for s in signals if datetime.fromisoformat(s["expires_at"]) > now]

    report = {
        "report_date": now.strftime("%Y-%m-%d"),
        "report_time": now.isoformat(),
        "market_status": "24/7 运行中",
        "freqtrade_connected": False,
        "portfolio": None,
        "signals": {
            "active": len(active_signals),
            "expired": len(signals) - len(active_signals),
            "details": [],
        },
        "alerts": [],
        "summary": "",
    }

    # 尝试获取 Freqtrade 数据
    positions = ft_api_get("/status")
    profit = ft_api_get("/profit")
    balance = ft_api_get("/balance")

    if positions is not None:
        report["freqtrade_connected"] = True

        # 持仓概要
        total_pnl = 0
        pos_details = []
        for pos in (positions or []):
            pnl = pos.get("profit_abs", 0)
            total_pnl += pnl
            pair = pos.get("pair", "?")
            symbol = pair.replace("/", "").replace(":USDT", "")
            side = "SHORT" if pos.get("is_short", False) else "LONG"
            leverage = pos.get("leverage", 1)

            # 爆仓距离
            entry = pos.get("open_rate", 0)
            current = pos.get("current_rate", 0)
            side_lower = side.lower()
            liq_price = calc_liquidation_price(entry, leverage, side_lower) if entry else 0
            liq_dist = calc_liquidation_distance(current, liq_price) if current else 0

            pos_details.append({
                "symbol": symbol,
                "side": side,
                "leverage": leverage,
                "entry_price": entry,
                "current_price": current,
                "pnl_pct": round(pos.get("profit_pct", 0) * 100, 2),
                "pnl_usdt": round(pnl, 2),
                "liquidation_distance_pct": liq_dist,
            })

        report["portfolio"] = {
            "open_positions": len(positions or []),
            "total_unrealized_pnl_usdt": round(total_pnl, 2),
            "positions": pos_details,
        }

        # 盈亏统计
        if profit:
            report["portfolio"]["profit_all_usdt"] = profit.get("profit_all_coin", 0)
            report["portfolio"]["profit_all_pct"] = profit.get("profit_all_pct", 0)
            report["portfolio"]["trade_count"] = profit.get("trade_count", 0)
            report["portfolio"]["winning_trades"] = profit.get("winning_trades", 0)
            report["portfolio"]["losing_trades"] = profit.get("losing_trades", 0)

        # 余额
        if balance:
            for b in balance.get("currencies", []):
                if b.get("currency") == "USDT":
                    report["portfolio"]["usdt_balance"] = b.get("balance", 0)
                    report["portfolio"]["usdt_free"] = b.get("free", 0)

        # 告警
        report["alerts"] = _build_position_alerts(positions or [], active_signals)
    else:
        report["summary"] = "Freqtrade 未运行"

    # 信号详情
    for s in active_signals:
        report["signals"]["details"].append({
            "symbol": s["symbol"],
            "action": s["action"],
            "leverage": s.get("leverage"),
            "confidence": s.get("confidence"),
            "expires_at": s.get("expires_at"),
        })

    # 生成摘要
    if report["freqtrade_connected"] and report["portfolio"]:
        p = report["portfolio"]
        alert_count = len(report["alerts"])
        report["summary"] = (
            f"持仓 {p['open_positions']} 个，"
            f"未实现盈亏 {p['total_unrealized_pnl_usdt']:+.2f} USDT。"
            f"{'无告警。' if alert_count == 0 else f'{alert_count} 条告警。'}"
            f"活跃信号 {report['signals']['active']} 个。"
        )

    if json_output:
        click.echo(json.dumps(report, indent=2, ensure_ascii=False))
        return

    # Rich 输出
    lines = [f"日期: {report['report_date']}"]
    lines.append(f"Freqtrade: {'已连接' if report['freqtrade_connected'] else '未连接'}")

    if report["portfolio"]:
        p = report["portfolio"]
        lines.append(f"持仓: {p['open_positions']} 个")
        lines.append(f"未实现盈亏: {p['total_unrealized_pnl_usdt']:+.2f} USDT")
        if "profit_all_usdt" in p:
            lines.append(f"总盈亏: {p['profit_all_usdt']:.2f} USDT ({p.get('profit_all_pct', 0):.2f}%)")
            lines.append(f"交易: {p.get('trade_count', 0)} 笔 (胜{p.get('winning_trades', 0)}/负{p.get('losing_trades', 0)})")
        if "usdt_balance" in p:
            lines.append(f"USDT 余额: {p['usdt_balance']:.2f} (可用: {p.get('usdt_free', 0):.2f})")

    lines.append(f"活跃信号: {report['signals']['active']} | 已过期: {report['signals']['expired']}")

    alert_count = len(report["alerts"])
    if alert_count:
        lines.append(f"[red]告警: {alert_count} 条[/red]")
    else:
        lines.append("[green]无告警[/green]")

    console.print(Panel("\n".join(lines), title="每日监控报告"))

    if report["summary"]:
        console.print(f"\n{report['summary']}")
