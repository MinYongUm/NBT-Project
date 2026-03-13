"""
NBT (Network Backup Tools)
FastAPI Web 서버 진입점 - v4.1

변경 이력:
    v4.0: FastAPI 초기 구성, SSE 백업 실행, 이력/Diff 조회
    v4.1: lifespan startup — settings.yaml → devices DB 자동 마이그레이션
          devices 라우터 추가
"""

import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from api.routers import history, diff, backup, pages, devices
from utils.db_manager import DBManager

logger = logging.getLogger(__name__)

_PROJECT_ROOT = Path(__file__).parent
_SETTINGS_PATH = _PROJECT_ROOT / "config" / "settings.yaml"


def _get_db() -> DBManager:
    """NBT_BACKUP_ROOT 환경변수로 DB 경로를 결정합니다."""
    backup_root = os.environ.get("NBT_BACKUP_ROOT", "/data/backup")
    db_path = Path(backup_root) / "nbt_history.db"
    return DBManager(db_path)


# ------------------------------------------------------------------
# Lifespan — 서버 시작/종료 시 실행
# ------------------------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI):
    """서버 시작 시 settings.yaml → devices DB 마이그레이션을 수행합니다."""
    db = _get_db()
    db.initialize()

    try:
        inserted = db.upsert_devices_from_yaml(_SETTINGS_PATH)
        if inserted > 0:
            logger.info(f"장비 마이그레이션 완료 — 신규 추가: {inserted}대")
        else:
            logger.info("장비 마이그레이션 — 신규 추가 없음 (이미 최신 상태)")
    except FileNotFoundError:
        logger.warning(
            "settings.yaml을 찾을 수 없음 — 장비 마이그레이션 건너뜀. "
            "웹 UI에서 직접 장비를 추가하세요."
        )
    finally:
        db.close()

    yield


# ------------------------------------------------------------------
# FastAPI 앱 초기화
# ------------------------------------------------------------------

app = FastAPI(
    title="NBT - Network Backup Tools",
    version="4.1",
    lifespan=lifespan,
)

# 정적 파일 및 템플릿 설정
app.mount("/static", StaticFiles(directory="web/static"), name="static")

app.include_router(history.router)
app.include_router(diff.router)
app.include_router(backup.router)
app.include_router(pages.router)
app.include_router(devices.router)