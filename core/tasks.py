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
    target_ips: Optional[list[str]] = None,  # 추가 (v4.4)
) -> dict:
    """백업 실행 Celery task.

    Args:
        run_id: DB에 이미 생성된 backup_runs.run_id
        group_filter: 특정 그룹만 실행 (mgmt/nexus/aci). None이면 전체.
        target_ips: 특정 IP 목록만 백업. None이면 전체 또는 group_filter 범위. (v4.4)

    Returns:
        dict: {"total": N, "success": N, "fail": N, "diff_count": N}
    """
    logger.info(
        f"백업 task 시작 | run_id={run_id} "
        f"group_filter={group_filter} target_ips={target_ips}"
    )
    publish_log(run_id, f"백업 시작 | run_id={run_id}", level="INFO")

    try:
        result = run_backup(
            group_filter=group_filter,
            run_id=run_id,
            log_callback=lambda msg: publish_log(run_id, msg),
            target_ips=target_ips,  # 추가 (v4.4)
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
        
@celery_app.task(
    bind=True,
    name="core.tasks.cleanup_task",
    max_retries=0,
    acks_late=True,
)
def cleanup_task(self: Task) -> dict:
    """보존 기간이 지난 백업 이력 및 파일을 정리합니다.

    환경변수:
        NBT_DB_RETENTION_DAYS  : DB 레코드 보존 기간 (기본 90일)
        NBT_FILE_RETENTION_DAYS: 백업 파일 보존 기간 (기본 365일)

    Returns:
        dict: {"deleted_runs": N, "deleted_folders": N}
    """
    from pathlib import Path
    from utils.db_manager import DBManager

    db_retention   = int(os.environ.get("NBT_DB_RETENTION_DAYS",   90))
    file_retention = int(os.environ.get("NBT_FILE_RETENTION_DAYS", 365))
    backup_root    = Path(os.environ.get("NBT_BACKUP_ROOT", "/data/backup"))

    logger.info(
        f"cleanup task 시작 | "
        f"DB={db_retention}일 / 파일={file_retention}일"
    )

    db_path = backup_root / "nbt_history.db"
    db = DBManager(db_path)
    db.initialize()

    try:
        # DB 레코드 정리
        deleted_runs = db.delete_old_runs(db_retention)

        # 백업 파일 정리
        deleted_folders = db.delete_old_backup_files(backup_root, file_retention)

        logger.info(
            f"cleanup task 완료 | "
            f"DB {deleted_runs}건 / 폴더 {deleted_folders}개 삭제"
        )
        return {
            "deleted_runs":    deleted_runs,
            "deleted_folders": deleted_folders,
        }

    except Exception as e:
        logger.error(f"cleanup task 예외 발생: {e}", exc_info=True)
        raise

    finally:
        db.close()        