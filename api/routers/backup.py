"""
NBT (Network Backup Tools) - Backup Router
- POST /api/backup              : 백업 실행 시작 (Celery task 등록, 202)
- WS   /api/backup/ws/{run_id}  : WebSocket 실시간 로그 스트림
- GET  /api/backup/file         : 백업 파일 내용 조회

중복 실행 방지:
    Redis nbt:is_running 플래그 (값: run_id 문자열)
    - TTL=420s: task_time_limit(360s) + 60s 버퍼
    - 워커 SIGKILL/OOM kill 시에도 자동 만료 보장
    - 409 응답에 블로킹 중인 run_id 포함 (디버깅 용이)
"""

import json
import logging
import os
from pathlib import Path
from typing import Optional

import redis.asyncio as aioredis
from fastapi import APIRouter, HTTPException, WebSocket, WebSocketDisconnect

from core.tasks import backup_task
from utils.db_manager import DBManager

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/backup", tags=["backup"])

REDIS_URL = os.environ.get("CELERY_BROKER_URL", "redis://redis:6379/0")

# nbt:is_running TTL (초)
# task_time_limit=360s + 60s 버퍼 = 420s
# 워커가 강제 종료되어 finally가 실행되지 않아도 자동 만료
_IS_RUNNING_TTL = 420


def _get_db() -> DBManager:
    """DB 경로를 환경변수에서 결정하고 DBManager를 반환합니다."""
    backup_root = os.environ.get("NBT_BACKUP_ROOT", "/data/backup")
    db_path = Path(backup_root) / "nbt_history.db"
    db = DBManager(db_path)
    db.initialize()
    return db


def _get_backup_root() -> Path:
    """백업 루트 경로를 반환합니다."""
    return Path(os.environ.get("NBT_BACKUP_ROOT", "/data/backup")).resolve()


@router.post("", status_code=202)
async def start_backup(group: Optional[str] = None):
    """백업을 Celery task로 등록합니다.

    Returns:
        202: {"run_id": N, "status": "queued"}
        409: 이미 백업 실행 중 (run_id 포함)
    """
    redis_client = aioredis.from_url(REDIS_URL, decode_responses=True)
    try:
        # 중복 실행 확인
        is_running = await redis_client.get("nbt:is_running")
        if is_running:
            raise HTTPException(
                status_code=409,
                detail=f"이미 백업이 실행 중입니다. (run_id={is_running})",
            )

        # run_id 생성
        db = _get_db()
        try:
            run_id = db.start_run()
        finally:
            db.close()

        # 중복 실행 방지 플래그 설정
        # - 값을 run_id로 저장: 잔존 시 어느 run이 블로킹 중인지 확인 가능
        # - ex=420: task_time_limit(360s) + 60s 버퍼
        #   워커 강제 종료(SIGKILL, OOM) 시 finally가 실행되지 않아도 자동 만료
        await redis_client.set("nbt:is_running", str(run_id), ex=_IS_RUNNING_TTL)

        # Celery task 등록
        backup_task.delay(run_id, group)

        logger.info(f"백업 task 등록: run_id={run_id}, group={group or 'all'}")
        return {"run_id": run_id, "status": "queued"}

    finally:
        await redis_client.aclose()


@router.get("/file")
async def get_backup_file(path: str):
    """백업 파일 내용을 반환합니다.

    보안 검증:
        - 요청 경로를 resolve()로 절대 경로 변환 (경로 탈출 차단)
        - NBT_BACKUP_ROOT 하위 경로인지 확인 (범위 외 접근 403)
        - 파일 존재 여부 확인 (없으면 404)

    Args:
        path: 백업 파일 절대 경로 (backup_results.file_path 값)

    Returns:
        {"path": str, "filename": str, "content": str}
    """
    backup_root = _get_backup_root()

    # 경로 탈출 공격 차단
    # resolve()는 .., 심볼릭 링크 등을 모두 실제 경로로 변환
    try:
        target = Path(path).resolve()
    except Exception:
        raise HTTPException(status_code=400, detail="잘못된 파일 경로입니다.")

    # NBT_BACKUP_ROOT 범위 외 접근 차단
    # is_relative_to(): target이 backup_root 하위 경로인지 검사
    if not target.is_relative_to(backup_root):
        logger.warning(f"허용 범위 외 파일 접근 시도: {path}")
        raise HTTPException(status_code=403, detail="접근이 허용되지 않은 경로입니다.")

    if not target.exists():
        raise HTTPException(status_code=404, detail="파일을 찾을 수 없습니다.")

    if not target.is_file():
        raise HTTPException(status_code=400, detail="파일이 아닙니다.")

    try:
        content = target.read_text(encoding="utf-8")
    except OSError as e:
        logger.error(f"파일 읽기 실패: {target}, {e}")
        raise HTTPException(status_code=500, detail="파일을 읽을 수 없습니다.")

    logger.info(f"백업 파일 조회: {target.name}")
    return {
        "path":     str(target),
        "filename": target.name,
        "content":  content,
    }


@router.websocket("/ws/{run_id}")
async def backup_websocket(websocket: WebSocket, run_id: int):
    """백업 실행 로그를 WebSocket으로 실시간 스트리밍합니다.

    - Redis pub/sub으로 nbt:log:{run_id} 채널 구독
    - level == "DONE" 수신 시 구독 해제 및 연결 종료
    """
    await websocket.accept()
    redis_client = aioredis.from_url(REDIS_URL, decode_responses=True)
    pubsub = redis_client.pubsub()
    channel = f"nbt:log:{run_id}"

    try:
        await pubsub.subscribe(channel)
        logger.info(f"WebSocket 연결: run_id={run_id}")

        async for message in pubsub.listen():
            # Redis pubsub은 subscribe 확인 메시지도 전달 — type이 message인 것만 처리
            if message["type"] != "message":
                continue

            try:
                data = json.loads(message["data"])
            except (json.JSONDecodeError, TypeError):
                continue

            await websocket.send_text(json.dumps(data))

            # 백업 완료 신호 수신 시 연결 종료
            if data.get("level") == "DONE":
                logger.info(f"백업 완료 수신, WebSocket 종료: run_id={run_id}")
                break

    except WebSocketDisconnect:
        logger.info(f"WebSocket 클라이언트 연결 종료: run_id={run_id}")
    except Exception as e:
        logger.error(f"WebSocket 오류: run_id={run_id}, {e}", exc_info=True)
    finally:
        await pubsub.unsubscribe(channel)
        await pubsub.aclose()
        await redis_client.aclose()