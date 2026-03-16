"""
NBT (Network Backup Tools) - Celery Tasks
- 백업 작업을 Celery task로 등록
- 실행 로그를 Redis pub/sub으로 publish
- WebSocket 핸들러(backup.py)가 subscribe하여 브라우저에 전달
"""

import json
import logging
import os
from typing import Optional

import redis as redis_client
from celery import Task
from celery.utils.log import get_task_logger

from core.celery_app import celery_app
from core.backup import run_backup

logger = get_task_logger(__name__)

REDIS_URL = os.environ.get("CELERY_BROKER_URL", "redis://redis:6379/0")
RUNNING_KEY = "nbt:is_running"


def _get_redis() -> redis_client.Redis:
    """Redis 클라이언트를 반환합니다."""
    return redis_client.Redis.from_url(REDIS_URL, decode_responses=True)


def publish_log(run_id: int, message: str, level: str = "INFO") -> None:
    """Redis 채널에 로그 메시지를 publish합니다.

    채널명: nbt:log:{run_id}
    메시지 형식: JSON {"level": "INFO", "message": "..."}
    """
    try:
        r = _get_redis()
        channel = f"nbt:log:{run_id}"
        payload = json.dumps({"level": level, "message": message})
        r.publish(channel, payload)
    except Exception as e:
        logger.warning(f"Redis publish 실패: {e}")


def clear_running_flag() -> None:
    """백업 완료 후 Redis 실행 중 플래그를 해제합니다."""
    try:
        r = _get_redis()
        r.delete(RUNNING_KEY)
        logger.info("Redis 실행 중 플래그 해제 완료")
    except Exception as e:
        logger.warning(f"Redis 플래그 해제 실패: {e}")


@celery_app.task(
    bind=True,
    name="core.tasks.backup_task",
    max_retries=0,
    acks_late=True,
)
def backup_task(
    self: Task,
    run_id: int,
    group_filter: Optional[str] = None,
) -> dict:
    """백업 실행 Celery task.

    Args:
        run_id: DB에 이미 생성된 backup_runs.run_id
        group_filter: 특정 그룹만 실행 (mgmt/nexus/aci). None이면 전체.

    Returns:
        dict: {"total": N, "success": N, "fail": N, "diff_count": N}
    """
    logger.info(f"백업 task 시작 | run_id={run_id} group_filter={group_filter}")
    publish_log(run_id, f"백업 시작 | run_id={run_id}", level="INFO")

    try:
        result = run_backup(
            group_filter=group_filter,
            run_id=run_id,
            log_callback=lambda msg: publish_log(run_id, msg),
        )
        publish_log(run_id, "[DONE]", level="DONE")
        return result

    except Exception as e:
        error_msg = f"백업 task 예외 발생: {e}"
        logger.error(error_msg, exc_info=True)
        publish_log(run_id, f"[ERROR] {error_msg}", level="ERROR")
        publish_log(run_id, "[DONE]", level="DONE")
        raise

    finally:
        clear_running_flag()