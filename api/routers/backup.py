"""
NBT (Network Backup Tools) - Backup Router
- Version: 4.2
- POST /api/backup              : Celery task 등록, run_id 반환
- GET  /api/backup/ws/{run_id}  : WebSocket — Redis subscribe → 브라우저 실시간 전송
"""

import json
import logging
import os
from typing import Optional

import redis.asyncio as aioredis
from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse

from core.tasks import backup_task
from utils.db_manager import DBManager
from utils.folder_create import get_backup_root

logger = logging.getLogger(__name__)

router = APIRouter()

REDIS_URL = os.environ.get("CELERY_BROKER_URL", "redis://redis:6379/0")
RUNNING_KEY = "nbt:is_running"


def _get_db() -> DBManager:
    db_path = get_backup_root() / "nbt_history.db"
    db = DBManager(db_path)
    db.initialize()
    return db


# ------------------------------------------------------------------
# POST /api/backup
# ------------------------------------------------------------------

@router.post("/api/backup", status_code=202)
async def start_backup(group: Optional[str] = None):
    """백업 실행 요청을 받아 Celery task를 등록합니다.

    실행 중 중복 요청은 409 Conflict를 반환합니다.

    Returns:
        {"run_id": N, "status": "queued"}
    """
    r = aioredis.from_url(REDIS_URL)
    try:
        is_running = await r.get(RUNNING_KEY)
        if is_running:
            return JSONResponse(
                status_code=409,
                content={"detail": "이미 백업이 실행 중입니다."}
            )

        db = _get_db()
        try:
            run_id = db.start_run()
        finally:
            db.close()

        await r.set(RUNNING_KEY, "1")
        backup_task.delay(run_id=run_id, group_filter=group)

        logger.info(f"백업 task 등록 완료: run_id={run_id} group={group}")
        return {"run_id": run_id, "status": "queued"}

    finally:
        await r.aclose()


# ------------------------------------------------------------------
# WebSocket /api/backup/ws/{run_id}
# ------------------------------------------------------------------

@router.websocket("/api/backup/ws/{run_id}")
async def backup_ws(websocket: WebSocket, run_id: int):
    """Redis pub/sub을 구독하여 백업 로그를 WebSocket으로 전달합니다.

    흐름:
        1. WebSocket 연결 수락
        2. Redis nbt:log:{run_id} 채널 구독
        3. 메시지 수신 → 브라우저로 전송
        4. [DONE] 수신 또는 연결 종료 시 구독 해제
    """
    await websocket.accept()
    logger.info(f"WebSocket 연결: run_id={run_id}")

    redis = aioredis.from_url(REDIS_URL, decode_responses=True)
    pubsub = redis.pubsub()
    channel = f"nbt:log:{run_id}"

    try:
        await pubsub.subscribe(channel)
        logger.info(f"Redis 채널 구독: {channel}")

        async for raw_message in pubsub.listen():
            if raw_message["type"] != "message":
                continue

            data = raw_message["data"]

            try:
                payload = json.loads(data)
                message = payload.get("message", data)
                level = payload.get("level", "INFO")
            except (json.JSONDecodeError, TypeError):
                message = data
                level = "INFO"

            await websocket.send_json({"level": level, "message": message})

            if level == "DONE":
                logger.info(f"백업 완료 신호 수신: run_id={run_id}")
                break

    except WebSocketDisconnect:
        logger.info(f"WebSocket 연결 해제: run_id={run_id}")

    except Exception as e:
        logger.error(f"WebSocket 오류: run_id={run_id} {e}", exc_info=True)

    finally:
        await pubsub.unsubscribe(channel)
        await pubsub.close()
        await redis.aclose()
        logger.info(f"Redis 구독 해제: {channel}")