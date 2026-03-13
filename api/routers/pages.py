"""
NBT (Network Backup Tools) - Page Router
브라우저에 HTML 페이지를 반환하는 라우터
"""

from fastapi import APIRouter, Request
from fastapi.templating import Jinja2Templates

router = APIRouter(tags=["pages"])
templates = Jinja2Templates(directory="web/templates")


@router.get("/")
def dashboard(request: Request):
    return templates.TemplateResponse(
        "index.html", {"request": request, "active": "dashboard"}
    )


@router.get("/backup")
def backup_page(request: Request):
    return templates.TemplateResponse(
        "backup.html", {"request": request, "active": "backup"}
    )


@router.get("/history")
def history_page(request: Request):
    return templates.TemplateResponse(
        "history.html", {"request": request, "active": "history"}
    )


@router.get("/diff")
def diff_page(request: Request):
    return templates.TemplateResponse(
        "diff.html", {"request": request, "active": "diff"}
    )


@router.get("/devices")
def devices_page(request: Request):
    return templates.TemplateResponse(
        "devices.html", {"request": request, "active": "devices"}
    )