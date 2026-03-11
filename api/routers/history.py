"""
NBT (Network Backup Tools) - History Router
GET /api/history       : 최근 백업 실행 이력 조회
GET /api/history/{run_id} : 특정 run의 장비별 결과 조회
"""

import os
from pathlib import Path

from fastapi import APIRouter, HTTPException

from utils.db_manager import DBManager

router = APIRouter(prefix="/api/history", tags=["history"])


def _get_db() -> DBManager:
    """DB 경로를 환경변수에서 결정하고 DBManager를 반환합니다."""
    backup_root = os.environ.get("NBT_BACKUP_ROOT", "/data/backup")
    db_path = Path(backup_root) / "nbt_history.db"

    if not db_path.exists():
        raise HTTPException(status_code=404, detail="백업 이력이 없습니다.")

    db = DBManager(db_path)
    db.initialize()
    return db


@router.get("")
def get_history(limit: int = 5):
    """최근 백업 실행 이력을 반환합니다."""
    db = _get_db()
    try:
        runs = db.get_recent_runs(limit)
        return [dict(row) for row in runs]
    finally:
        db.close()


@router.get("/{run_id}")
def get_run_detail(run_id: int):
    """특정 run의 장비별 결과를 반환합니다."""
    db = _get_db()
    try:
        results = db.get_run_results(run_id)
        if not results:
            raise HTTPException(
                status_code=404,
                detail=f"run_id={run_id} 결과가 없습니다.",
            )
        return [dict(row) for row in results]
    finally:
        db.close()