"""CLI: cryptobot web — Web Dashboard"""

import click


@click.group()
def web():
    """Web Dashboard"""
    pass


@web.command("start")
@click.option("--host", default="0.0.0.0", help="绑定地址")
@click.option("--port", default=8000, type=int, help="端口")
@click.option("--reload", "auto_reload", is_flag=True, help="开发模式自动重载")
def start(host: str, port: int, auto_reload: bool):
    """启动 Web Dashboard"""
    import uvicorn
    from cryptobot.web.app import create_app

    app = create_app()
    click.echo(f"Dashboard: http://{host}:{port}")
    uvicorn.run(app, host=host, port=port, reload=auto_reload)
