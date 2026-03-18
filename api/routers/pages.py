"""
NBT (Network Backup Tools) - Page Router
브라우저에 HTML 페이지를 반환하는 라우터

v4.3 변경:
    - 모든 응답에 Cache-Control: no-store 추가
      로그아웃 후 뒤로 가기 시 캐시된 페이지가 표시되는 현상 방지
v5.1 변경:
    - /analyze 페이지 추가
"""

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

router = APIRouter(tags=["pages"])
templates = Jinja2Templates(directory="web/templates")

# 로그아웃 후 뒤로 가기 시 브라우저 캐시 페이지 표시 방지
_NO_CACHE = {"Cache-Control": "no-store"}


@router.get("/", response_class=HTMLResponse)
def dashboard(request: Request):
    response = templates.TemplateResponse(
        "index.html", {"request": request, "active": "dashboard"}
    )
    response.headers.update(_NO_CACHE)
    return response


@router.get("/backup", response_class=HTMLResponse)
def backup_page(request: Request):
    response = templates.TemplateResponse(
        "backup.html", {"request": request, "active": "backup"}
    )
    response.headers.update(_NO_CACHE)
    return response


@router.get("/history", response_class=HTMLResponse)
def history_page(request: Request):
    response = templates.TemplateResponse(
        "history.html", {"request": request, "active": "history"}
    )
    response.headers.update(_NO_CACHE)
    return response


@router.get("/diff", response_class=HTMLResponse)
def diff_page(request: Request):
    response = templates.TemplateResponse(
        "diff.html", {"request": request, "active": "diff"}
    )
    response.headers.update(_NO_CACHE)
    return response


@router.get("/devices", response_class=HTMLResponse)
def devices_page(request: Request):
    response = templates.TemplateResponse(
        "devices.html", {"request": request, "active": "devices"}
    )
    response.headers.update(_NO_CACHE)
    return response


@router.get("/analyze", response_class=HTMLResponse)
def analyze_page(request: Request):                          # 추가 (v5.1)
    response = templates.TemplateResponse(
        "analyze.html", {"request": request, "active": "analyze"}
    )
    response.headers.update(_NO_CACHE)
    return response