"""健康检查: cryptobot doctor — 检查运行环境"""

import json
import os
import shutil
import sys

import click
from rich.console import Console

from cryptobot.config import CONFIG_DIR, DATA_OUTPUT_DIR, load_pairs

console = Console()

# ─── 检查项定义 ────────────────────────────────────────────────────────────

CHECK_ITEMS = [
    ("Python 3.12", "python_version", "FAIL"),
    ("TA-Lib C 库", "talib_import", "FAIL"),
    ("Claude CLI", "claude_cli", "WARN"),
    ("settings.yaml", "settings_yaml", "FAIL"),
    ("pairs.yaml + 币种数", "pairs_yaml", "FAIL"),
    ("BINANCE_API_KEY", "env_binance_key", "FAIL"),
    ("BINANCE_API_SECRET", "env_binance_secret", "FAIL"),
    ("COINGLASS_API_KEY", "env_coinglass", "WARN"),
    ("TELEGRAM 配置", "env_telegram", "WARN"),
    ("Binance API 连通", "binance_ping", "WARN"),
    ("Freqtrade API 连通", "freqtrade_ping", "WARN"),
    ("数据目录可写", "data_dir_writable", "FAIL"),
]


def _check_python_version() -> tuple[str, str]:
    v = sys.version_info
    version_str = f"{v.major}.{v.minor}.{v.micro}"
    if v.major == 3 and v.minor >= 12:
        return "OK", f"Python {version_str}"
    return "FAIL", f"Python {version_str} (需要 3.12+)"


def _check_talib_import() -> tuple[str, str]:
    try:
        import talib  # noqa: F401
        return "OK", "TA-Lib 已安装"
    except ImportError:
        return "FAIL", "TA-Lib 未安装 (pip install TA-Lib)"


def _check_claude_cli() -> tuple[str, str]:
    if shutil.which("claude"):
        return "OK", "claude CLI 可用"
    return "WARN", "claude CLI 未找到"


def _check_settings_yaml() -> tuple[str, str]:
    path = CONFIG_DIR / "settings.yaml"
    if path.exists():
        return "OK", str(path)
    return "FAIL", f"{path} 不存在"


def _check_pairs_yaml() -> tuple[str, str]:
    try:
        pairs = load_pairs()
        count = len(pairs.get("pairs", []))
        if count > 0:
            return "OK", f"{count} 个交易对"
        return "FAIL", "pairs.yaml 为空"
    except Exception as e:
        return "FAIL", f"pairs.yaml 解析失败: {e}"


def _check_env_var(name: str) -> tuple[str, str]:
    if os.environ.get(name):
        return "OK", f"{name} 已设置"
    return "FAIL", f"{name} 未设置"


def _check_telegram() -> tuple[str, str]:
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    if token and chat_id:
        return "OK", "TELEGRAM_BOT_TOKEN + TELEGRAM_CHAT_ID 已设置"
    missing = []
    if not token:
        missing.append("TELEGRAM_BOT_TOKEN")
    if not chat_id:
        missing.append("TELEGRAM_CHAT_ID")
    return "WARN", f"{', '.join(missing)} 未设置"


def _check_binance_ping() -> tuple[str, str]:
    try:
        import httpx
        resp = httpx.get("https://fapi.binance.com/fapi/v1/ping", timeout=5)
        if resp.status_code == 200:
            return "OK", "Binance Futures API 连通"
        return "WARN", f"Binance API 状态码 {resp.status_code}"
    except Exception as e:
        return "WARN", f"Binance API 不可达: {e}"


def _check_freqtrade_ping() -> tuple[str, str]:
    try:
        from cryptobot.freqtrade_api import ft_api_get
        result = ft_api_get("/ping")
        if result is not None:
            return "OK", "Freqtrade API 连通"
        return "WARN", "Freqtrade API 不可达"
    except Exception as e:
        return "WARN", f"Freqtrade API 错误: {e}"


def _check_data_dir_writable() -> tuple[str, str]:
    if not DATA_OUTPUT_DIR.exists():
        return "FAIL", f"{DATA_OUTPUT_DIR} 不存在"
    try:
        test_file = DATA_OUTPUT_DIR / ".doctor_test"
        test_file.write_text("ok")
        test_file.unlink()
        return "OK", f"{DATA_OUTPUT_DIR} 可写"
    except OSError as e:
        return "FAIL", f"{DATA_OUTPUT_DIR} 不可写: {e}"


# ─── 运行全部检查 ──────────────────────────────────────────────────────────

_CHECKER_MAP = {
    "python_version": _check_python_version,
    "talib_import": _check_talib_import,
    "claude_cli": _check_claude_cli,
    "settings_yaml": _check_settings_yaml,
    "pairs_yaml": _check_pairs_yaml,
    "env_binance_key": lambda: _check_env_var("BINANCE_API_KEY"),
    "env_binance_secret": lambda: _check_env_var("BINANCE_API_SECRET"),
    "env_coinglass": lambda: _check_env_var("COINGLASS_API_KEY"),
    "env_telegram": _check_telegram,
    "binance_ping": _check_binance_ping,
    "freqtrade_ping": _check_freqtrade_ping,
    "data_dir_writable": _check_data_dir_writable,
}


def run_checks() -> list[dict]:
    """运行全部检查项，返回结果列表"""
    results = []
    for label, checker_key, fail_level in CHECK_ITEMS:
        checker = _CHECKER_MAP[checker_key]
        try:
            status, detail = checker()
        except Exception as e:
            status, detail = fail_level, f"检查异常: {e}"

        # WARN 类检查项 FAIL 时降级为 WARN
        if status == "FAIL" and fail_level == "WARN":
            status = "WARN"

        results.append({
            "name": label,
            "status": status,
            "detail": detail,
        })
    return results


def print_results(results: list[dict]) -> None:
    """Rich 格式输出检查结果"""
    for r in results:
        status = r["status"]
        if status == "OK":
            tag = "[green][OK][/green]"
        elif status == "WARN":
            tag = "[yellow][WARN][/yellow]"
        else:
            tag = "[red][FAIL][/red]"
        console.print(f"  {tag} {r['name']}: {r['detail']}")


# ─── CLI ────────────────────────────────────────────────────────────────────

@click.command()
@click.option("--json-output", is_flag=True, help="JSON 格式输出")
def doctor(json_output: bool):
    """检查运行环境"""
    results = run_checks()

    if json_output:
        click.echo(json.dumps(results, ensure_ascii=False, indent=2))
    else:
        console.print("[bold]环境健康检查[/bold]\n")
        print_results(results)

        fails = sum(1 for r in results if r["status"] == "FAIL")
        warns = sum(1 for r in results if r["status"] == "WARN")
        console.print()
        if fails:
            console.print(f"[red]{fails} 项失败, {warns} 项警告[/red]")
        elif warns:
            console.print(f"[green]全部通过[/green] ({warns} 项警告)")
        else:
            console.print("[green]全部通过[/green]")

    has_fail = any(r["status"] == "FAIL" for r in results)
    raise SystemExit(1 if has_fail else 0)
