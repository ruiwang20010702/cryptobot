"""HTML 视图路由 (Jinja2)"""

from fastapi import APIRouter, Request

router = APIRouter()


@router.get("/")
def dashboard(request: Request):
    """Dashboard 主页"""
    templates = request.app.state.templates
    return templates.TemplateResponse(request, "dashboard.html")
