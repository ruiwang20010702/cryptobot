"""CLI 命令组"""

import click

from cryptobot.cli.data import data
from cryptobot.cli.signal import signal
from cryptobot.cli.portfolio import portfolio
from cryptobot.cli.monitor import monitor
from cryptobot.cli.indicator import indicator
from cryptobot.cli.workflow import workflow
from cryptobot.cli.realtime import realtime
from cryptobot.cli.scheduler import daemon
from cryptobot.cli.journal import journal
from cryptobot.cli.events import events
from cryptobot.cli.backtest import backtest
from cryptobot.cli.web import web
from cryptobot.cli.doctor import doctor
from cryptobot.cli.init_cmd import init_cmd
from cryptobot.cli.prompt import prompt
from cryptobot.cli.archive import archive


@click.group()
@click.version_option(package_name="cryptobot")
def cli():
    """加密货币合约量化交易系统 CLI"""
    from cryptobot.config import load_settings
    from cryptobot.logging_config import setup_logging

    settings = load_settings()
    log_cfg = settings.get("logging", {})
    setup_logging(
        level=log_cfg.get("level", "INFO"),
        json_format=log_cfg.get("json_format", True),
        log_file=log_cfg.get("log_file"),
    )


cli.add_command(data)
cli.add_command(signal)
cli.add_command(portfolio)
cli.add_command(monitor)
cli.add_command(indicator)
cli.add_command(workflow)
cli.add_command(realtime)
cli.add_command(daemon)
cli.add_command(journal)
cli.add_command(events)
cli.add_command(backtest)
cli.add_command(web)
cli.add_command(doctor)
cli.add_command(init_cmd)
cli.add_command(prompt)
cli.add_command(archive)
