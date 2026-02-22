"""HTML 视图路由 (Jinja2)"""

from fastapi import APIRouter, Request

router = APIRouter()


@router.get("/")
def dashboard(request: Request):
    """Dashboard 主页"""
    templates = request.app.state.templates
    return templates.TemplateResponse(request, "dashboard.html")


@router.get("/edge")
def edge_page(request: Request):
    """Edge 仪表盘页面"""
    templates = request.app.state.templates
    return templates.TemplateResponse(request, "edge.html")
