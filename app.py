"""
NBT (Network Backup Tools) - FastAPI 애플리케이션 진입점
- Version: v4.3

변경 이력:
    v4.0: FastAPI 전환, SSE 실시간 로그
    v4.1: devices 라우터, settings.yaml → DB 마이그레이션
    v4.2: Celery + Redis + WebSocket, nbt:is_running 플래그
    v4.2.1: Redis TTL 420s, SQLite WAL 모드
    v4.3: JWT 인증, 로그인/로그아웃
"""

import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path

import redis.asyncio as aioredis
from fastapi import Depends, FastAPI, Request
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles

from api.routers import analyze, backup, devices, diff, history, pages
from api.routers import auth as auth_router
from core.auth import NotAuthenticatedException, get_current_user, get_current_user_api
from utils.db_manager import DBManager

logger = logging.getLogger(__name__)

# ------------------------------------------------------------------
# 경로 설정
# ------------------------------------------------------------------

_PROJECT_ROOT  = Path(__file__).parent
_STATIC_DIR    = _PROJECT_ROOT / "web" / "static"
_SETTINGS_PATH = _PROJECT_ROOT / "config" / "settings.yaml"


# ------------------------------------------------------------------
# Lifespan (시작 / 종료 처리)
# ------------------------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI):
    """애플리케이션 시작/종료 시 실행되는 lifespan 핸들러."""

    # ── 시작 처리 ─────────────────────────────────────────────────

    # 1. 인증 환경변수 확인
    _check_auth_env()

    # 2. Redis 연결 확인 + nbt:is_running 플래그 초기화
    redis_url = os.environ.get("CELERY_BROKER_URL", "redis://redis:6379/0")
    try:
        r = aioredis.from_url(redis_url, decode_responses=True)
        await r.ping()
        await r.delete("nbt:is_running")
        logger.info("Redis 연결 확인 완료, nbt:is_running 플래그 초기화")
        await r.aclose()
    except Exception as e:
        logger.warning(f"Redis 연결 실패: {e} — Celery 기능 사용 불가")

    # 3. settings.yaml → devices DB 자동 마이그레이션
    backup_root = os.environ.get("NBT_BACKUP_ROOT", "/data/backup")
    db_path = Path(backup_root) / "nbt_history.db"
    db = DBManager(db_path)
    db.initialize()
    try:
        if _SETTINGS_PATH.exists():
            migrated = db.upsert_devices_from_yaml(_SETTINGS_PATH)
            if migrated:
                logger.info(f"settings.yaml → DB 마이그레이션 완료: {migrated}대")
        else:
            logger.info("settings.yaml 없음 — DB 마이그레이션 건너뜀")
    except Exception as e:
        logger.warning(f"devices 마이그레이션 실패: {e}")
    finally:
        db.close()

    logger.info("NBT Web 서버 시작 완료")
    yield

    # ── 종료 처리 ─────────────────────────────────────────────────
    logger.info("NBT Web 서버 종료")


def _check_auth_env() -> None:
    """인증 필수 환경변수를 확인합니다.

    누락 시 서버를 시작하지 않고 즉시 종료합니다.
    운영 환경에서 인증 없이 서버가 뜨는 상황을 원천 차단합니다.
    """
    missing = []
    if not os.environ.get("ADMIN_PASSWORD"):
        missing.append("ADMIN_PASSWORD")
    if not os.environ.get("JWT_SECRET"):
        missing.append("JWT_SECRET")

    if missing:
        msg = (
            f"필수 인증 환경변수 누락: {', '.join(missing)}\n"
            "  → .env 파일에 해당 변수를 추가하세요."
        )
        logger.critical(msg)
        raise EnvironmentError(msg)

    logger.info("인증 환경변수 확인 완료")


# ------------------------------------------------------------------
# FastAPI 인스턴스
# ------------------------------------------------------------------

app = FastAPI(
    title="NBT — Network Backup Tools",
    version="5.0.0",
    docs_url="/docs",
    lifespan=lifespan,
)

# ------------------------------------------------------------------
# Exception Handler (v4.3 추가)
# ------------------------------------------------------------------

@app.exception_handler(NotAuthenticatedException)
async def not_authenticated_handler(request: Request, exc: NotAuthenticatedException):
    """페이지 인증 실패 → /login 리다이렉트.

    get_current_user()가 raise한 NotAuthenticatedException을 잡아서
    브라우저를 /login으로 보냅니다.
    Depends() 안에서 RedirectResponse를 반환하면 동작하지 않으므로
    exception_handler 패턴을 사용합니다.
    """
    return RedirectResponse(url="/login")


# ------------------------------------------------------------------
# Static 파일
# ------------------------------------------------------------------

if _STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(_STATIC_DIR)), name="static")


# ------------------------------------------------------------------
# 라우터 등록
# ------------------------------------------------------------------

# 인증 없음 — /login, /api/auth/login, /api/auth/logout
app.include_router(auth_router.router)

# 페이지 라우터 — 인증 실패 시 /login 리다이렉트
app.include_router(
    pages.router,
    dependencies=[Depends(get_current_user)],
)

# API 라우터 — 인증 실패 시 401 JSON 반환
app.include_router(
    history.router,
    dependencies=[Depends(get_current_user_api)],
)
app.include_router(
    diff.router,
    dependencies=[Depends(get_current_user_api)],
)
app.include_router(
    backup.router,
    dependencies=[Depends(get_current_user_api)],
)
app.include_router(
    devices.router,
    dependencies=[Depends(get_current_user_api)],
)

app.include_router(
    analyze.router,
    dependencies=[Depends(get_current_user_api)],
)