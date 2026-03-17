"""
NBT (Network Backup Tools) - Celery Application
- Celery 앱 인스턴스 정의
- broker/backend: Redis (환경변수로 주입)
- task_soft_time_limit: 좀비 SSH 세션 방지
- beat_schedule: 정기 자동 백업 + cleanup 스케줄 (v4.4)
"""

import os

from celery import Celery
from celery.schedules import crontab

BROKER_URL     = os.environ.get("CELERY_BROKER_URL",     "redis://redis:6379/0")
RESULT_BACKEND = os.environ.get("CELERY_RESULT_BACKEND", "redis://redis:6379/0")

# 자동 백업 실행 시각 (환경변수로 주입, 기본값: 매일 새벽 2시)
_SCHEDULE_HOUR   = os.environ.get("NBT_SCHEDULE_HOUR",   "2")
_SCHEDULE_MINUTE = os.environ.get("NBT_SCHEDULE_MINUTE", "0")

# cleanup 실행 시각 — 백업(02:00) 완료 후 1시간 뒤 고정
# 백업 완료 후 실행해야 당일 생성된 파일이 보존 기간 계산에 포함됨
_CLEANUP_HOUR   = "3"
_CLEANUP_MINUTE = "0"

celery_app = Celery(
    "nbt",
    broker=BROKER_URL,
    backend=RESULT_BACKEND,
    include=["core.tasks"],
)

celery_app.conf.update(
    # 타임존
    timezone="Asia/Seoul",
    enable_utc=True,

    # 좀비 SSH 세션 방지
    task_soft_time_limit=300,
    task_time_limit=360,

    # 작업 결과 24시간 후 자동 삭제
    result_expires=86400,

    # 공정한 작업 분배
    worker_prefetch_multiplier=1,

    # 직렬화
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],

    beat_schedule={
        # 정기 자동 백업 (v4.4)
        # 매일 NBT_SCHEDULE_HOUR:NBT_SCHEDULE_MINUTE에 전체 백업 실행
        "nbt-auto-backup": {
            "task":     "core.tasks.backup_task",
            "schedule": crontab(hour=_SCHEDULE_HOUR, minute=_SCHEDULE_MINUTE),
            "args":     [],
            "kwargs":   {
                "run_id":       None,
                "group_filter": None,
                "target_ips":   None,
            },
        },
        # 보존 기간 자동 정리 (v4.4)
        # 매일 03:00 KST — 백업 완료(02:00) 후 1시간 뒤 실행
        # NBT_DB_RETENTION_DAYS / NBT_FILE_RETENTION_DAYS 환경변수로 기간 설정
        "nbt-cleanup": {
            "task":     "core.tasks.cleanup_task",
            "schedule": crontab(hour=_CLEANUP_HOUR, minute=_CLEANUP_MINUTE),
        },
    },
)