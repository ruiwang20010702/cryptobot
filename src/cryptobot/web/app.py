"""FastAPI 应用工厂"""

from pathlib import Path

from fastapi import FastAPI
from fastapi.templating import Jinja2Templates

from cryptobot.web.routes.api import router as api_router
from cryptobot.web.routes.views import router as views_router

TEMPLATES_DIR = Path(__file__).parent / "templates"
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))


def create_app() -> FastAPI:
    """创建 FastAPI 应用"""
    app = FastAPI(title="CryptoBot Dashboard", version="0.1.0")
    app.include_router(api_router, prefix="/api")
    app.include_router(views_router)
    # 共享 templates 实例给 views router
    app.state.templates = templates
    return app
