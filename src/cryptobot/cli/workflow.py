"""工作流命令: 运行自动化分析"""

import json
import logging
import time

import click
from rich.console import Console
from rich.panel import Panel

console = Console()
logger = logging.getLogger(__name__)


@click.group()
def workflow():
    """自动化分析工作流"""
    pass


@workflow.command("run")
@click.option("--dry-run", is_flag=True, help="只运行数据采集和筛选（不调用 LLM）")
@click.option("--json-output", is_flag=True, help="输出 JSON 格式（适合 cron 日志）")
@click.option("--verbose", "-v", is_flag=True, help="详细日志")
def run(dry_run: bool, json_output: bool, verbose: bool):
    """运行完整分析工作流"""
    if verbose:
        logging.basicConfig(level=logging.DEBUG, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    else:
        logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    start = time.time()

    if dry_run:
        _run_dry(json_output)
    else:
        _run_full(json_output)

    elapsed = time.time() - start
    if not json_output:
        console.print(f"\n耗时: {elapsed:.1f}s")


def _run_dry(json_output: bool):
    """dry-run: 只运行 collect_data + screen"""
    from cryptobot.workflow.graph import collect_data, screen

    if not json_output:
        console.print("[cyan]Dry-run 模式: 只运行数据采集和筛选[/cyan]\n")

    # collect_data
    if not json_output:
        console.print("采集市场数据...")
    state = collect_data({})

    # screen
    if not json_output:
        console.print("筛选交易标的...")
    state.update(screen(state))

    result = {
        "mode": "dry_run",
        "screened_symbols": state.get("screened_symbols", []),
        "symbols_data_count": len(state.get("market_data", {})),
        "fear_greed": state.get("fear_greed", {}).get("current_value"),
        "errors": state.get("errors", []),
    }

    if json_output:
        click.echo(json.dumps(result, indent=2, ensure_ascii=False))
        return

    console.print(Panel(
        f"数据采集: {result['symbols_data_count']} 币种\n"
        f"恐惧贪婪: {result['fear_greed']}\n"
        f"筛选结果: {', '.join(result['screened_symbols'])}\n"
        f"错误: {len(result['errors'])} 个",
        title="Dry-Run 结果",
    ))

    if result["errors"]:
        for e in result["errors"]:
            console.print(f"  [yellow]! {e}[/yellow]")


def _run_full(json_output: bool):
    """完整运行工作流"""
    from cryptobot.workflow.graph import build_graph

    if not json_output:
        console.print("[cyan]运行完整分析工作流...[/cyan]\n")

    app = build_graph()

    # 运行图
    final_state = app.invoke({})

    result = {
        "mode": "full",
        "screened_symbols": final_state.get("screened_symbols", []),
        "analyses_count": len(final_state.get("analyses", {})),
        "research_count": len(final_state.get("research", {})),
        "decisions": final_state.get("decisions", []),
        "approved_signals": final_state.get("approved_signals", []),
        "executed": final_state.get("executed", []),
        "errors": final_state.get("errors", []),
    }

    if json_output:
        click.echo(json.dumps(result, indent=2, ensure_ascii=False, default=str))
        return

    # Rich 输出
    lines = [
        f"筛选币种: {', '.join(result['screened_symbols'])}",
        f"分析完成: {result['analyses_count']} 币种",
        f"研究完成: {result['research_count']} 币种",
        f"交易决策: {len(result['decisions'])} 个",
        f"通过风控: {len(result['approved_signals'])} 个",
        f"信号写入: {len(result['executed'])} 个",
        f"错误: {len(result['errors'])} 个",
    ]
    console.print(Panel("\n".join(lines), title="工作流运行结果"))

    # 显示执行的信号
    for sig in result["executed"]:
        direction = sig.get("action", "?")
        symbol = sig.get("symbol", "?")
        leverage = sig.get("leverage", "?")
        confidence = sig.get("confidence", "?")
        color = "green" if direction == "long" else "red"
        entry = sig.get("entry_price_range", [])
        sl = sig.get("stop_loss", "?")
        tp = sig.get("take_profit", [])
        size = sig.get("position_size_pct", "?")
        summary = sig.get("analysis_summary", {})
        risk_score = summary.get("risk_score", "?")

        console.print(f"\n  [{color}]{direction.upper()}[/{color}] {symbol} "
                       f"{leverage}x  置信度:{confidence}  风险:{risk_score}")
        if entry:
            console.print(f"    入场: {entry[0]:.2f} - {entry[1]:.2f}" if len(entry) == 2 else f"    入场: {entry}")
        console.print(f"    止损: {sl}  止盈: {', '.join(str(t) for t in tp) if tp else '?'}")
        console.print(f"    仓位: {size}%")
        reasoning = summary.get("reasoning", "")
        if reasoning:
            console.print(f"    理由: {reasoning[:120]}")
        warnings = summary.get("warnings", [])
        if warnings:
            console.print(f"    [yellow]警告: {'; '.join(warnings[:3])}[/yellow]")

    if result["errors"]:
        console.print("\n[yellow]错误:[/yellow]")
        for e in result["errors"]:
            console.print(f"  ! {e}")


@workflow.command("re-review")
@click.option("--json-output", is_flag=True, help="输出 JSON 格式")
@click.option("--verbose", "-v", is_flag=True, help="详细日志")
def re_review_cmd(json_output: bool, verbose: bool):
    """重新评估现有持仓，生成止损调整建议"""
    if verbose:
        logging.basicConfig(level=logging.DEBUG, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    else:
        logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    start = time.time()

    # 1. 获取 Freqtrade 持仓
    positions = _get_freqtrade_positions()
    if not positions:
        if json_output:
            click.echo(json.dumps({"suggestions": [], "message": "无持仓"}, ensure_ascii=False))
        else:
            console.print("[yellow]无持仓需要复审[/yellow]")
        return

    if not json_output:
        console.print(f"[cyan]发现 {len(positions)} 个持仓，开始复审...[/cyan]\n")

    # 2. 仅采集持仓币种的最新市场数据
    from cryptobot.workflow.graph import collect_data_for_symbols

    held_symbols = [p["pair"].replace("/", "").replace(":USDT", "") for p in positions]
    state = collect_data_for_symbols(held_symbols)

    # 3. AI 复审
    from cryptobot.workflow.graph import re_review

    suggestions = re_review(positions, state)

    # 4. 对 adjust_stop_loss 建议更新 signal.json
    from cryptobot.signal.bridge import update_signal_field

    from cryptobot.notify import notify_stop_loss_adjusted

    for s in suggestions:
        if s["decision"] == "adjust_stop_loss" and s.get("new_stop_loss"):
            updated = update_signal_field(s["symbol"], "stop_loss", s["new_stop_loss"])
            s["signal_updated"] = updated
            if updated:
                notify_stop_loss_adjusted(s["symbol"], None, s["new_stop_loss"])
                if not json_output:
                    console.print(
                        f"  [yellow]已更新 {s['symbol']} 止损 → {s['new_stop_loss']}[/yellow]"
                    )

    elapsed = time.time() - start

    if json_output:
        click.echo(json.dumps({
            "suggestions": suggestions,
            "elapsed_seconds": round(elapsed, 1),
        }, indent=2, ensure_ascii=False, default=str))
        return

    # Rich 输出
    for s in suggestions:
        symbol = s["symbol"]
        decision = s["decision"]
        reasoning = s.get("reasoning", "")
        risk = s.get("risk_level", "?")

        if decision == "hold":
            color = "green"
            icon = "HOLD"
        elif decision == "adjust_stop_loss":
            color = "yellow"
            icon = "ADJUST SL"
        else:
            color = "red"
            icon = "CLOSE"

        console.print(f"\n  [{color}]{icon}[/{color}] {symbol} (风险: {risk})")
        if s.get("new_stop_loss"):
            console.print(f"    新止损: {s['new_stop_loss']}")
        if reasoning:
            console.print(f"    理由: {reasoning[:150]}")

    console.print(f"\n耗时: {elapsed:.1f}s")


def _get_freqtrade_positions() -> list[dict]:
    """从 Freqtrade API 获取当前持仓"""
    from cryptobot.freqtrade_api import ft_api_get

    return ft_api_get("/status") or []
