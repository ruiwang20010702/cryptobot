"""FastAPI 应用工厂"""

import os
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.templating import Jinja2Templates

from cryptobot.config import load_settings
from cryptobot.web.routes.api import router as api_router
from cryptobot.web.routes.views import router as views_router

TEMPLATES_DIR = Path(__file__).parent / "templates"
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

# 不需要认证的路径前缀
_PUBLIC_PREFIXES = ("/docs", "/openapi.json", "/redoc")


def _get_auth_token() -> str:
    """获取 Dashboard 认证 token"""
    token = os.environ.get("DASHBOARD_TOKEN", "")
    if token:
        return token
    settings = load_settings()
    return settings.get("web", {}).get("auth_token", "")


def _is_local(request: Request) -> bool:
    """判断请求是否来自本地"""
    client = request.client
    if client is None:
        return False
    return client.host in ("127.0.0.1", "::1", "localhost", "testclient")


def create_app() -> FastAPI:
    """创建 FastAPI 应用"""
    app = FastAPI(title="CryptoBot Dashboard", version="0.1.0")

    @app.middleware("http")
    async def auth_middleware(request: Request, call_next):
        path = request.url.path

        # 静态资源和文档不需要认证
        if any(path.startswith(p) for p in _PUBLIC_PREFIXES):
            return await call_next(request)

        token = _get_auth_token()
        if token:
            # 有 token 时验证 Authorization header
            auth = request.headers.get("Authorization", "")
            if auth != f"Bearer {token}":
                return JSONResponse(
                    status_code=401,
                    content={"detail": "Unauthorized"},
                )
        else:
            # 无 token 时仅允许本地访问
            if not _is_local(request):
                return JSONResponse(
                    status_code=403,
                    content={"detail": "Remote access requires auth_token"},
                )

        return await call_next(request)

    app.include_router(api_router, prefix="/api")
    app.include_router(views_router)
    # 共享 templates 实例给 views router
    app.state.templates = templates
    return app
