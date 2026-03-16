"""
NBT (Network Backup Tools) - Celery Application
- Celery 앱 인스턴스 정의
- broker/backend: Redis (환경변수로 주입)
- task_soft_time_limit: 좀비 SSH 세션 방지
"""

import os

from celery import Celery

# 환경변수에서 Redis 주소를 읽음
# docker-compose.yml에서 CELERY_BROKER_URL / CELERY_RESULT_BACKEND 주입
BROKER_URL = os.environ.get("CELERY_BROKER_URL", "redis://redis:6379/0")
RESULT_BACKEND = os.environ.get("CELERY_RESULT_BACKEND", "redis://redis:6379/0")

celery_app = Celery(
    "nbt",
    broker=BROKER_URL,
    backend=RESULT_BACKEND,
    include=["core.tasks"],  # task 정의 파일 위치
)

celery_app.conf.update(
    # 타임존
    timezone="Asia/Seoul",
    enable_utc=True,

    # 좀비 SSH 세션 방지
    # soft: 300초 초과 시 SoftTimeLimitExceeded 예외 발생 → 정상 종료 유도
    # hard: 360초 초과 시 강제 종료 (soft 처리 실패 대비)
    task_soft_time_limit=300,
    task_time_limit=360,

    # 작업 결과 24시간 후 자동 삭제 (Redis 메모리 관리)
    result_expires=86400,

    # worker가 한 번에 가져올 작업 수
    # 1로 설정하면 작업 하나 완료 후 다음 작업을 가져옴 (공정한 분배)
    worker_prefetch_multiplier=1,

    # 작업 직렬화 형식
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
)