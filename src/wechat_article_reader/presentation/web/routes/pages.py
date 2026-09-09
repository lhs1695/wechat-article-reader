"""页面路由 — 渲染 HTML 页面"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

TEMPLATES_DIR = Path(__file__).parent.parent / "templates"

templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
router = APIRouter()


@router.get("/", response_class=HTMLResponse)
def page_home(request: Request):
    return templates.TemplateResponse(request, "home.html")


@router.get("/article", response_class=HTMLResponse)
def page_article(request: Request):
    return templates.TemplateResponse(request, "article.html")


@router.get("/batch", response_class=HTMLResponse)
def page_batch(request: Request):
    return templates.TemplateResponse(request, "batch.html")


@router.get("/history", response_class=HTMLResponse)
def page_history(request: Request):
    return templates.TemplateResponse(request, "history.html")
