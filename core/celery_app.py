"""
NBT (Network Backup Tools) - Celery Application
- Celery 앱 인스턴스 정의
- broker/backend: Redis (환경변수로 주입)
- task_soft_time_limit: 좀비 SSH 세션 방지
- beat_schedule: 정기 자동 백업 스케줄 (v4.4)
"""

import os

from celery import Celery
from celery.schedules import crontab

BROKER_URL     = os.environ.get("CELERY_BROKER_URL",     "redis://redis:6379/0")
RESULT_BACKEND = os.environ.get("CELERY_RESULT_BACKEND", "redis://redis:6379/0")

# 자동 백업 실행 시각 (환경변수로 주입, 기본값: 매일 새벽 2시)
_SCHEDULE_HOUR   = os.environ.get("NBT_SCHEDULE_HOUR",   "2")
_SCHEDULE_MINUTE = os.environ.get("NBT_SCHEDULE_MINUTE", "0")

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

    # 정기 자동 백업 스케줄 (v4.4)
    # crontab(hour="2", minute="0") → 매일 02:00 KST 전체 백업 실행
    # NBT_SCHEDULE_HOUR / NBT_SCHEDULE_MINUTE 환경변수로 시각 변경 가능
    beat_schedule={
        "nbt-auto-backup": {
            "task":     "core.tasks.backup_task",
            "schedule": crontab(hour=_SCHEDULE_HOUR, minute=_SCHEDULE_MINUTE),
            "args":     [],      # run_id=None → backup_task 내부에서 생성
            "kwargs":   {
                "run_id":      None,
                "group_filter": None,
                "target_ips":  None,
            },
        },
    },
)