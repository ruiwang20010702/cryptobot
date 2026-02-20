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


@click.group()
@click.version_option(package_name="cryptobot")
def cli():
    """加密货币合约量化交易系统 CLI"""
    pass


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
