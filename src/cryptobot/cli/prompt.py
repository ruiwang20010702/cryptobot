"""CLI: prompt 版本管理命令组"""

import click
from rich.console import Console
from rich.table import Table

console = Console()


@click.group()
def prompt():
    """Prompt 版本管理"""
    pass


@prompt.command("list")
def list_versions():
    """列出所有 prompt 版本"""
    from cryptobot.evolution.prompt_manager import list_versions as _list

    data = _list()
    active = data["active_version"]

    table = Table(title="Prompt Versions")
    table.add_column("Version", style="cyan")
    table.add_column("Active", justify="center")
    table.add_column("Note")
    table.add_column("Addons")
    table.add_column("Created")

    for ver, info in sorted(data["versions"].items()):
        is_active = "*" if ver == active else ""
        addon_roles = ", ".join(info.get("addons", {}).keys()) or "—"
        created = info.get("created_at", "")[:19]
        table.add_row(ver, is_active, info.get("note", ""), addon_roles, created)

    console.print(table)


@prompt.command("new-version")
@click.option("--note", required=True, help="版本说明")
@click.option("--addon-trader", default=None, help="TRADER 角色 addon")
@click.option("--addon-risk", default=None, help="RISK_MANAGER 角色 addon")
@click.option("--addon-analyst", default=None, help="通用分析师 addon")
def new_version(note: str, addon_trader: str | None, addon_risk: str | None,
                addon_analyst: str | None):
    """创建新 prompt 版本"""
    from cryptobot.evolution.prompt_manager import create_version

    addons = {}
    if addon_trader:
        addons["TRADER"] = addon_trader
    if addon_risk:
        addons["RISK_MANAGER"] = addon_risk
    if addon_analyst:
        addons["ANALYST"] = addon_analyst

    ver = create_version(note, addons)
    console.print(f"[green]已创建版本 {ver}[/green]: {note}")


@prompt.command("activate")
@click.argument("version")
def activate(version: str):
    """切换活跃 prompt 版本"""
    from cryptobot.evolution.prompt_manager import activate_version
    from cryptobot.workflow.prompts import reset_prompt_version_cache

    if activate_version(version):
        reset_prompt_version_cache()
        console.print(f"[green]已切换到版本 {version}[/green]")
    else:
        console.print(f"[red]版本 {version} 不存在[/red]")


@prompt.command("show")
@click.argument("version", required=False)
def show(version: str | None):
    """查看版本详情"""
    from cryptobot.evolution.prompt_manager import (
        get_active_version, get_version_detail,
    )

    if version is None:
        version = get_active_version()

    detail = get_version_detail(version)
    if detail is None:
        console.print(f"[red]版本 {version} 不存在[/red]")
        return

    console.print(f"[cyan]版本: {version}[/cyan]")
    console.print(f"  说明: {detail.get('note', '')}")
    console.print(f"  创建: {detail.get('created_at', '')}")

    addons = detail.get("addons", {})
    if addons:
        console.print("  Addons:")
        for role, text in addons.items():
            preview = text[:80] + "..." if len(text) > 80 else text
            console.print(f"    {role}: {preview}")
    else:
        console.print("  Addons: (无)")
