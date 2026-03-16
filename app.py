"""
NBT (Network Backup Tools) - FastAPI Web Server
- Version: 4.2
- 진입점: uvicorn app:app --host 0.0.0.0 --port 8000 --reload

Update History:
- ver 4.0: FastAPI 백엔드 + Jinja2 Web UI
- ver 4.1: lifespan 추가 — settings.yaml → devices DB 자동 마이그레이션
- ver 4.2: Redis 연결 확인 추가, WebSocket 지원 (backup 라우터에서 처리)
"""

import logging
import os
from contextlib import asynccontextmanager

import redis.asyncio as aioredis
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from api.routers import backup, devices, diff, history, pages
from utils.db_manager import DBManager
from utils.folder_create import get_backup_root

logger = logging.getLogger(__name__)

REDIS_URL = os.environ.get("CELERY_BROKER_URL", "redis://redis:6379/0")


# ------------------------------------------------------------------
# Lifespan
# ------------------------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI):
    """서버 시작/종료 시 실행되는 lifespan 핸들러."""

    # --- startup ---

    # 1. Redis 연결 확인
    try:
        r = aioredis.from_url(REDIS_URL)
        await r.ping()
        await r.aclose()
        logger.info(f"Redis 연결 확인 완료: {REDIS_URL}")
    except Exception as e:
        logger.error(f"Redis 연결 실패: {e} — Celery task 기능 불가")
        
    # 2. settings.yaml → devices DB 자동 마이그레이션 (1회, 중복 무시)
    try:
        from pathlib import Path
        db_path = get_backup_root() / "nbt_history.db"
        settings_path = Path("/app/config/settings.yaml")
        db = DBManager(db_path)
        db.initialize()
        db.upsert_devices_from_yaml(settings_path)
        db.close()
        logger.info("devices DB 마이그레이션 완료")
    except Exception as e:
        logger.warning(f"devices DB 마이그레이션 실패 (settings.yaml 없을 수 있음): {e}")

    yield

    # --- shutdown ---
    logger.info("NBT 서버 종료")


# ------------------------------------------------------------------
# App
# ------------------------------------------------------------------

app = FastAPI(
    title="NBT (Network Backup Tools)",
    version="4.2",
    lifespan=lifespan,
)

# Static 파일
app.mount("/static", StaticFiles(directory="web/static"), name="static")

# 라우터 등록
app.include_router(history.router)
app.include_router(diff.router)
app.include_router(backup.router)
app.include_router(devices.router)
app.include_router(pages.router)