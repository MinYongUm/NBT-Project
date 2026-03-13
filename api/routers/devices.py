"""
NBT (Network Backup Tools) - Devices Router
GET    /api/devices         : 전체 장비 목록 조회 (활성 장비)
POST   /api/devices         : 장비 추가
PUT    /api/devices/{id}    : 장비 수정
DELETE /api/devices/{id}    : 장비 비활성화 (소프트 삭제)
"""

import os
import sqlite3
from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, field_validator

from utils.db_manager import DBManager

router = APIRouter(prefix="/api/devices", tags=["devices"])


# ------------------------------------------------------------------
# Request body 모델
# ------------------------------------------------------------------

class DeviceBody(BaseModel):
    """장비 추가/수정 시 요청 본문 구조."""
    group_name:  str
    device_type: str
    ip:          str
    description: str = ""

    @field_validator("group_name", "device_type", "ip")
    @classmethod
    def must_not_be_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("빈 값은 허용되지 않습니다.")
        return v.strip()


# ------------------------------------------------------------------
# Helper
# ------------------------------------------------------------------

def _get_db() -> DBManager:
    """DB 경로를 환경변수에서 결정하고 DBManager를 반환합니다."""
    backup_root = os.environ.get("NBT_BACKUP_ROOT", "/data/backup")
    db_path = Path(backup_root) / "nbt_history.db"

    if not db_path.exists():
        raise HTTPException(status_code=404, detail="DB 파일이 없습니다.")

    db = DBManager(db_path)
    db.initialize()
    return db


# ------------------------------------------------------------------
# Endpoints
# ------------------------------------------------------------------

@router.get("")
def get_devices():
    """활성 장비 목록을 반환합니다."""
    db = _get_db()
    try:
        rows = db.get_all_devices(include_inactive=False)
        return [dict(row) for row in rows]
    finally:
        db.close()


@router.post("", status_code=201)
def add_device(body: DeviceBody):
    """새 장비를 추가합니다.

    Returns:
        201: {"id": <생성된 장비 id>}
        409: IP 중복 시
    """
    db = _get_db()
    try:
        device_id = db.add_device(
            group_name=body.group_name,
            device_type=body.device_type,
            ip=body.ip,
            description=body.description,
        )
        return {"id": device_id}
    except sqlite3.IntegrityError:
        raise HTTPException(
            status_code=409,
            detail=f"이미 등록된 IP입니다: {body.ip}",
        )
    finally:
        db.close()


@router.put("/{device_id}")
def update_device(device_id: int, body: DeviceBody):
    """장비 정보를 수정합니다.

    Returns:
        200: {"id": device_id}
        404: 대상 장비 없음
        409: IP 중복 시
    """
    db = _get_db()
    try:
        updated = db.update_device(
            device_id=device_id,
            group_name=body.group_name,
            device_type=body.device_type,
            ip=body.ip,
            description=body.description,
        )
        if not updated:
            raise HTTPException(
                status_code=404,
                detail=f"장비를 찾을 수 없습니다: id={device_id}",
            )
        return {"id": device_id}
    except sqlite3.IntegrityError:
        raise HTTPException(
            status_code=409,
            detail=f"이미 등록된 IP입니다: {body.ip}",
        )
    finally:
        db.close()


@router.delete("/{device_id}")
def deactivate_device(device_id: int):
    """장비를 비활성화합니다 (소프트 삭제).

    실제 행을 삭제하지 않고 is_active=0으로 변경합니다.
    백업 이력과의 연결을 유지하기 위함입니다.

    Returns:
        200: {"id": device_id}
        404: 대상 장비 없음
    """
    db = _get_db()
    try:
        updated = db.deactivate_device(device_id)
        if not updated:
            raise HTTPException(
                status_code=404,
                detail=f"장비를 찾을 수 없습니다: id={device_id}",
            )
        return {"id": device_id}
    finally:
        db.close()