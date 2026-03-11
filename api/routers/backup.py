"""
NBT (Network Backup Tools) - Backup Router
POST /api/backup        : 백업 실행 시작 (BackgroundTasks)
GET  /api/backup/stream : 실시간 로그 수신 (SSE)
"""

import queue
import threading
from typing import Optional

from fastapi import APIRouter, BackgroundTasks
from fastapi.responses import StreamingResponse

from core.backup import run_backup

router = APIRouter(prefix="/api/backup", tags=["backup"])

# 모듈 레벨 Queue — POST와 GET /stream이 공유
_log_queue: queue.Queue = queue.Queue()
_backup_lock = threading.Lock()  # 동시에 두 번 실행 방지


@router.post("")
def start_backup(
    background_tasks: BackgroundTasks,
    group: Optional[str] = None,
):
    """백업을 백그라운드에서 실행합니다."""
    if not _backup_lock.acquire(blocking=False):
        return {"status": "already_running", "message": "백업이 이미 실행 중입니다."}

    # Queue 초기화 (이전 실행 잔여 메시지 제거)
    while not _log_queue.empty():
        _log_queue.get_nowait()

    def _run():
        try:
            run_backup(group_filter=group, log_queue=_log_queue)
        finally:
            _log_queue.put("[DONE]")
            _backup_lock.release()

    background_tasks.add_task(_run)
    return {"status": "started", "group": group or "all"}


@router.get("/stream")
def stream_logs():
    """백업 실행 로그를 SSE로 스트리밍합니다."""

    def _generate():
        while True:
            try:
                msg = _log_queue.get(timeout=60)
                yield f"data: {msg}\n\n"
                if msg == "[DONE]":
                    break
            except queue.Empty:
                # 60초 동안 메시지 없으면 연결 종료
                yield "data: [TIMEOUT]\n\n"
                break

    return StreamingResponse(
        _generate(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache"},
    )