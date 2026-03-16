"""
NBT (Network Backup Tools) - Auth Module
- JWT 기반 인증 (python-jose)
- hmac.compare_digest 비밀번호 검증 (타이밍 공격 방지)
- HttpOnly 쿠키 방식

환경변수:
    ADMIN_PASSWORD  : 관리자 비밀번호 (필수)
    JWT_SECRET      : JWT 서명 키 (필수)
    JWT_EXPIRE_HOURS: 토큰 유효 시간, 기본 8시간 (선택)
"""

import hmac
import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import Cookie
from jose import JWTError, jwt

logger = logging.getLogger(__name__)

ALGORITHM = "HS256"


# ------------------------------------------------------------------
# 커스텀 예외 — 페이지 라우터 인증 실패 시 사용
# app.py의 exception_handler가 /login으로 리다이렉트
# ------------------------------------------------------------------

class NotAuthenticatedException(Exception):
    """인증되지 않은 접근 — 페이지 라우터 전용."""
    pass


# ------------------------------------------------------------------
# 환경변수 로더
# ------------------------------------------------------------------

def _get_jwt_secret() -> str:
    secret = os.environ.get("JWT_SECRET", "")
    if not secret:
        raise EnvironmentError(
            "JWT_SECRET 환경변수가 설정되지 않았습니다.\n"
            "  → .env 파일에 JWT_SECRET=<랜덤 문자열> 을 추가하세요."
        )
    return secret


def _get_admin_password() -> str:
    pw = os.environ.get("ADMIN_PASSWORD", "")
    if not pw:
        raise EnvironmentError(
            "ADMIN_PASSWORD 환경변수가 설정되지 않았습니다.\n"
            "  → .env 파일에 ADMIN_PASSWORD=<비밀번호> 를 추가하세요."
        )
    return pw


def _get_expire_hours() -> int:
    try:
        return int(os.environ.get("JWT_EXPIRE_HOURS", "8"))
    except ValueError:
        logger.warning("JWT_EXPIRE_HOURS 값이 올바르지 않습니다. 기본값 8시간으로 설정합니다.")
        return 8


# ------------------------------------------------------------------
# 비밀번호 검증
# ------------------------------------------------------------------

def verify_password(plain_password: str) -> bool:
    """입력된 비밀번호를 ADMIN_PASSWORD와 비교합니다.

    hmac.compare_digest()는 문자열 길이에 무관하게 일정 시간이
    소요되므로 타이밍 공격을 방지합니다.
    .env에 평문으로 저장하는 단일 계정 구조이므로
    bcrypt 해싱 없이 이 방식으로 충분합니다.
    """
    admin_password = _get_admin_password()
    return hmac.compare_digest(
        plain_password.encode("utf-8"),
        admin_password.encode("utf-8"),
    )


# ------------------------------------------------------------------
# JWT
# ------------------------------------------------------------------

def create_access_token() -> str:
    """JWT 액세스 토큰을 생성합니다."""
    expire = datetime.now(timezone.utc) + timedelta(hours=_get_expire_hours())
    payload = {
        "sub": "nbt-admin",
        "exp": expire,
    }
    token = jwt.encode(payload, _get_jwt_secret(), algorithm=ALGORITHM)
    logger.info("JWT 토큰 발급 완료")
    return token


def _decode_token(token: str) -> Optional[str]:
    """JWT 토큰을 검증하고 sub 값을 반환합니다."""
    if not token:
        return None
    try:
        payload = jwt.decode(token, _get_jwt_secret(), algorithms=[ALGORITHM])
        return payload.get("sub")
    except JWTError as e:
        logger.debug(f"JWT 검증 실패: {e}")
        return None


# ------------------------------------------------------------------
# FastAPI 의존성 함수
# ------------------------------------------------------------------

async def get_current_user(
    access_token: Optional[str] = Cookie(default=None),
) -> str:
    """페이지 라우터 전용 인증 의존성.

    인증 실패 시 NotAuthenticatedException을 raise합니다.
    app.py의 exception_handler가 /login으로 리다이렉트합니다.
    """
    sub = _decode_token(access_token)
    if not sub:
        logger.debug("페이지 인증 실패 — NotAuthenticatedException raise")
        raise NotAuthenticatedException()
    return sub


async def get_current_user_api(
    access_token: Optional[str] = Cookie(default=None),
) -> str:
    """API 엔드포인트 전용 인증 의존성.

    인증 실패 시 401 JSON을 반환합니다.
    프론트엔드 JS의 handle401()이 /login으로 이동합니다.
    """
    from fastapi import HTTPException, status

    sub = _decode_token(access_token)
    if not sub:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="인증이 필요합니다.",
        )
    return sub