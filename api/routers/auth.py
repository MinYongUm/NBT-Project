"""
NBT (Network Backup Tools) - Auth Router
- 로그인 / 로그아웃 엔드포인트
- JWT HttpOnly 쿠키 발급 및 삭제

엔드포인트:
    GET  /login           — 로그인 페이지 HTML
    POST /api/auth/login  — 비밀번호 검증 → JWT 쿠키 발급
    POST /api/auth/logout — JWT 쿠키 삭제
"""

import logging
from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel

from core.auth import create_access_token, verify_password

logger = logging.getLogger(__name__)

# 템플릿 디렉토리 경로
_TEMPLATES_DIR = Path(__file__).parent.parent.parent / "web" / "templates"
templates = Jinja2Templates(directory=str(_TEMPLATES_DIR))

router = APIRouter(tags=["auth"])


# ------------------------------------------------------------------
# Request 스키마
# ------------------------------------------------------------------

class LoginRequest(BaseModel):
    password: str


# ------------------------------------------------------------------
# 로그인 페이지
# ------------------------------------------------------------------

@router.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    """로그인 페이지 HTML을 반환합니다.

    이미 로그인된 상태면 /로 리다이렉트합니다.
    """
    from fastapi.responses import RedirectResponse
    from core.auth import _decode_token

    token = request.cookies.get("access_token")
    if token and _decode_token(token):
        return RedirectResponse(url="/")

    return templates.TemplateResponse("login.html", {"request": request})


# ------------------------------------------------------------------
# 로그인 처리
# ------------------------------------------------------------------

@router.post("/api/auth/login")
async def login(body: LoginRequest):
    """비밀번호를 검증하고 JWT 쿠키를 발급합니다.

    성공: 200 + Set-Cookie (access_token, HttpOnly, SameSite=Lax)
    실패: 401 + { "detail": "..." }
    """
    if not verify_password(body.password):
        logger.warning("로그인 실패 — 비밀번호 불일치")
        return JSONResponse(
            status_code=401,
            content={"detail": "비밀번호가 올바르지 않습니다."},
        )

    token = create_access_token()
    logger.info("로그인 성공 — JWT 쿠키 발급")

    response = JSONResponse(
        status_code=200,
        content={"status": "ok"},
    )
    response.set_cookie(
        key="access_token",
        value=token,
        httponly=True,       # JavaScript에서 접근 불가
        samesite="lax",      # CSRF 방지
        secure=False,        # HTTP 환경 (HTTPS 전환 시 True로 변경)
    )
    return response


# ------------------------------------------------------------------
# 로그아웃 처리
# ------------------------------------------------------------------

@router.post("/api/auth/logout")
async def logout():
    """JWT 쿠키를 만료시켜 로그아웃 처리합니다.

    쿠키 삭제는 max_age=0으로 즉시 만료하는 방식을 사용합니다.
    """
    logger.info("로그아웃 — JWT 쿠키 삭제")

    response = JSONResponse(
        status_code=200,
        content={"status": "ok"},
    )
    response.set_cookie(
        key="access_token",
        value="",
        httponly=True,
        samesite="lax",
        secure=False,
        max_age=0,           # 즉시 만료
    )
    return response