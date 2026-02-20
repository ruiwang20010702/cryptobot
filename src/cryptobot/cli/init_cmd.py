"""环境初始化: cryptobot init — 创建目录、生成 .env、运行健康检查"""

import shutil

import click
from rich.console import Console

from cryptobot.config import PROJECT_ROOT, DATA_OUTPUT_DIR

console = Console()

DIRS_TO_CREATE = [
    DATA_OUTPUT_DIR / "signals",
    DATA_OUTPUT_DIR / "journal",
    DATA_OUTPUT_DIR / ".cache",
    PROJECT_ROOT / "logs",
]

ENV_FILE = PROJECT_ROOT / ".env"
ENV_EXAMPLE = PROJECT_ROOT / ".env.example"


@click.command("init")
def init_cmd():
    """初始化运行环境"""
    # 1. 创建目录
    for d in DIRS_TO_CREATE:
        if d.exists():
            console.print(f"  目录已存在 {d.relative_to(PROJECT_ROOT)}")
        else:
            d.mkdir(parents=True, exist_ok=True)
            console.print(f"  创建目录 {d.relative_to(PROJECT_ROOT)}... done")

    # 2. 生成 .env
    if ENV_FILE.exists():
        console.print("  .env 已存在，跳过")
    elif ENV_EXAMPLE.exists():
        shutil.copy2(ENV_EXAMPLE, ENV_FILE)
        console.print("  从 .env.example 创建 .env... done")
    else:
        console.print("  [yellow].env.example 不存在，跳过[/yellow]")

    # 3. 交互填入 API key
    binance_key = click.prompt("Binance API Key", default="", show_default=False)
    binance_secret = click.prompt("Binance API Secret", default="", show_default=False)
    setup_tg = click.confirm("配置 Telegram 通知?", default=False)

    env_lines = []
    if binance_key:
        env_lines.append(f"BINANCE_API_KEY={binance_key}")
    if binance_secret:
        env_lines.append(f"BINANCE_API_SECRET={binance_secret}")
    if setup_tg:
        tg_token = click.prompt("Telegram Bot Token", default="", show_default=False)
        tg_chat = click.prompt("Telegram Chat ID", default="", show_default=False)
        if tg_token:
            env_lines.append(f"TELEGRAM_BOT_TOKEN={tg_token}")
        if tg_chat:
            env_lines.append(f"TELEGRAM_CHAT_ID={tg_chat}")

    if env_lines and ENV_FILE.exists():
        with open(ENV_FILE, "a") as f:
            f.write("\n" + "\n".join(env_lines) + "\n")
        console.print(f"  已追加 {len(env_lines)} 个配置到 .env")

    # 4. 运行 doctor
    console.print("\n运行健康检查...")
    from cryptobot.cli.doctor import run_checks, print_results

    results = print_results(run_checks())  # noqa: F841

    console.print("\n[green]初始化完成![/green]")
