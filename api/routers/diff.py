"""
NBT (Network Backup Tools) - Diff Router
GET /api/diff : 최근 Config Diff 이력 조회
"""

import os
from pathlib import Path

from fastapi import APIRouter, HTTPException

from utils.db_manager import DBManager

router = APIRouter(prefix="/api/diff", tags=["diff"])


def _get_db() -> DBManager:
    """DB 경로를 환경변수에서 결정하고 DBManager를 반환합니다."""
    backup_root = os.environ.get("NBT_BACKUP_ROOT", "/data/backup")
    db_path = Path(backup_root) / "nbt_history.db"

    if not db_path.exists():
        raise HTTPException(status_code=404, detail="Diff 이력이 없습니다.")

    db = DBManager(db_path)
    db.initialize()
    return db


@router.get("")
def get_diffs(limit: int = 10):
    """최근 Config Diff 이력을 반환합니다."""
    db = _get_db()
    try:
        diffs = db.get_recent_diffs(limit)
        return [dict(row) for row in diffs]
    finally:
        db.close()