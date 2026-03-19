"""
NBT (Network Backup Tools) - Celery Tasks
- 백업 작업을 Celery task로 등록
- 실행 로그를 Redis pub/sub으로 publish
- WebSocket 핸들러(backup.py)가 subscribe하여 브라우저에 전달

Tasks:
    backup_task   : SSH 백업 실행 (v5.2: auto_analyze 파라미터 추가)
    cleanup_task  : 보존 기간 초과 DB/파일 정리
    analysis_task : 백업 완료 후 Config 변경 장비 AI 분석 (v5.2 신규)
    report_task   : 분석 결과 HTML 리포트 메일 발송 (v5.2 신규)
"""

import json
import logging
import os
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional

import redis as redis_client
from celery import Task
from celery.utils.log import get_task_logger

from core.celery_app import celery_app
from core.backup import run_backup

logger = get_task_logger(__name__)

REDIS_URL  = os.environ.get("CELERY_BROKER_URL", "redis://redis:6379/0")
RUNNING_KEY = "nbt:is_running"
KST         = timezone(timedelta(hours=9))


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


# ------------------------------------------------------------------
# backup_task
# ------------------------------------------------------------------

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
    target_ips: Optional[list[str]] = None,
    auto_analyze: bool = False,            # v5.2 추가
) -> dict:
    """백업 실행 Celery task.

    Args:
        run_id:       DB에 이미 생성된 backup_runs.run_id
        group_filter: 특정 그룹만 실행 (mgmt/nexus/aci). None이면 전체.
        target_ips:   특정 IP 목록만 백업. None이면 전체 또는 group_filter 범위.
        auto_analyze: True이면 백업 완료 후 analysis_task를 자동 체이닝. (v5.2)
                      Beat 스케줄에서만 True로 설정. 수동 백업은 기본값 False.

    Returns:
        dict: {"run_id": N, "total": N, "success": N, "fail": N, "diff_count": N}
    """
    logger.info(
        f"백업 task 시작 | run_id={run_id} "
        f"group_filter={group_filter} target_ips={target_ips} "
        f"auto_analyze={auto_analyze}"
    )
    publish_log(run_id, f"백업 시작 | run_id={run_id}", level="INFO")

    try:
        result = run_backup(
            group_filter=group_filter,
            run_id=run_id,
            log_callback=lambda msg: publish_log(run_id, msg),
            target_ips=target_ips,
        )

        # run_id를 result에 포함 (v5.2: analysis_task 체이닝에 필요)
        result["run_id"] = run_id

        publish_log(run_id, "[DONE]", level="DONE")

        # auto_analyze=True이면 analysis_task 자동 체이닝 (v5.2)
        if auto_analyze:
            logger.info(f"analysis_task 체이닝 | run_id={run_id}")
            analysis_task.delay(run_id=run_id)

        return result

    except Exception as e:
        error_msg = f"백업 task 예외 발생: {e}"
        logger.error(error_msg, exc_info=True)
        publish_log(run_id, f"[ERROR] {error_msg}", level="ERROR")
        publish_log(run_id, "[DONE]", level="DONE")
        raise

    finally:
        clear_running_flag()


# ------------------------------------------------------------------
# cleanup_task
# ------------------------------------------------------------------

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
        deleted_runs    = db.delete_old_runs(db_retention)
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


# ------------------------------------------------------------------
# analysis_task (v5.2 신규)
# ------------------------------------------------------------------

@celery_app.task(
    bind=True,
    name="core.tasks.analysis_task",
    max_retries=0,
    acks_late=True,
)
def analysis_task(self: Task, run_id: int) -> dict:
    """백업 완료 후 Config 변경 장비를 자동 분석합니다. (v5.2)

    흐름:
        1. DB에서 해당 run의 diff 결과 조회 (변경 감지 장비 목록)
        2. DB에서 해당 run의 전체 결과 조회 (성공/실패 분류)
        3. 변경 감지 장비: RAG 인덱싱 → Ollama 분석
        4. 변경 없는 장비 / 백업 실패 장비: 분석 생략
        5. report_data 구성 → report_task 체이닝

    Args:
        run_id: 분석 대상 backup_runs.run_id

    Returns:
        dict: report_data (report_task로 전달되는 구조와 동일)
    """
    from utils.db_manager import DBManager
    from utils.config_loader import load_config
    from core.rag import index_config, search_config
    from core.analyzer import analyze_with_context

    backup_root = Path(os.environ.get("NBT_BACKUP_ROOT", "/data/backup"))
    db_path     = backup_root / "nbt_history.db"
    db          = DBManager(db_path)
    db.initialize()

    logger.info(f"analysis_task 시작 | run_id={run_id}")

    try:
        # ── 1. 이번 run 전체 결과 조회 ──────────────────────────────
        run_results = db.get_run_results(run_id)
        if not run_results:
            logger.warning(f"analysis_task: run_id={run_id} 결과 없음 — 종료")
            return {}

        # 성공 / 실패 장비 분류
        success_hostnames: set[str] = set()
        failed_hostnames:  list[str] = []

        for r in run_results:
            hostname = r["hostname"] or r["ip"]
            if r["status"] == "SUCCESS":
                success_hostnames.add(hostname)
            else:
                failed_hostnames.append(hostname)

        # ── 2. 이번 run의 diff 결과 조회 ────────────────────────────
        diff_results = db.get_run_diffs(run_id)

        # 변경 감지 장비 hostname set
        diff_hostnames: set[str] = {d["hostname"] for d in diff_results}

        # 변경 없는 장비 = 성공했지만 diff 없는 장비
        no_change_hostnames: list[str] = sorted(
            success_hostnames - diff_hostnames
        )

        logger.info(
            f"analysis_task | run_id={run_id} | "
            f"변경감지={len(diff_hostnames)}대 "
            f"변경없음={len(no_change_hostnames)}대 "
            f"실패={len(failed_hostnames)}대"
        )

        # ── 3. 변경 감지 장비 RAG 인덱싱 + Ollama 분석 ──────────────
        analyzed: list[dict] = []

        # 기본 분석 질문 — 운영자 관점 핵심 사항 요약 요청
        _DEFAULT_QUESTION = (
            "이 네트워크 장비의 Config 변경 내용을 분석해줘. "
            "어떤 설정이 변경됐는지, 운영 관점에서 주의해야 할 사항이 있는지 요약해줘."
        )

        for diff in diff_results:
            hostname     = diff["hostname"]
            ip           = diff["ip"]
            diff_lines   = diff["diff_lines"]
            current_file = diff["current_file"]

            logger.info(f"분석 시작: {hostname} ({diff_lines}줄 변경)")

            if not current_file or not Path(current_file).exists():
                logger.warning(f"분석 생략: {hostname} — 백업 파일 없음 ({current_file})")
                analyzed.append({
                    "hostname":   hostname,
                    "ip":         ip,
                    "diff_lines": diff_lines,
                    "analysis":   "백업 파일을 찾을 수 없어 분석을 수행하지 못했습니다.",
                })
                continue

            try:
                # RAG 인덱싱
                chunk_count = index_config(
                    file_path=current_file,
                    collection_name=hostname,
                )
                logger.info(f"인덱싱 완료: {hostname} ({chunk_count}청크)")

                # 유사 청크 검색
                chunks = search_config(
                    query=_DEFAULT_QUESTION,
                    collection_name=hostname,
                    n_results=5,
                )

                # Ollama 분석
                result = analyze_with_context(
                    chunks=chunks,
                    question=_DEFAULT_QUESTION,
                    file_name=hostname,
                )
                analysis_text = result.get("answer", "분석 결과 없음")

            except Exception as e:
                logger.error(f"분석 실패: {hostname} — {e}", exc_info=True)
                analysis_text = f"분석 중 오류가 발생했습니다: {e}"

            analyzed.append({
                "hostname":   hostname,
                "ip":         ip,
                "diff_lines": diff_lines,
                "analysis":   analysis_text,
            })

        # ── 4. run 요약 정보 조회 ────────────────────────────────────
        recent_runs = db.get_recent_runs(limit=1)
        summary_row = None
        for row in recent_runs:
            if row["run_id"] == run_id:
                summary_row = row
                break

        summary = {
            "total":   summary_row["total"]   if summary_row else len(run_results),
            "success": summary_row["success"] if summary_row else len(success_hostnames),
            "fail":    summary_row["fail"]    if summary_row else len(failed_hostnames),
            "diff":    len(diff_results),
        }

        # ── 5. report_data 구성 → report_task 체이닝 ────────────────
        date = datetime.now(KST).strftime("%Y-%m-%d")
        report_data = {
            "date":      date,
            "summary":   summary,
            "analyzed":  analyzed,
            "no_change": no_change_hostnames,
            "failed":    failed_hostnames,
        }

        logger.info(f"analysis_task 완료 | run_id={run_id} → report_task 체이닝")
        report_task.delay(report_data=report_data)

        return report_data

    except Exception as e:
        logger.error(f"analysis_task 예외 발생: {e}", exc_info=True)
        raise

    finally:
        db.close()


# ------------------------------------------------------------------
# report_task (v5.2 신규)
# ------------------------------------------------------------------

@celery_app.task(
    bind=True,
    name="core.tasks.report_task",
    max_retries=0,
    acks_late=True,
)
def report_task(self: Task, report_data: dict) -> dict:
    """분석 결과 HTML 리포트를 생성하고 메일로 발송합니다. (v5.2)

    Args:
        report_data: analysis_task가 구성한 리포트 데이터 dict

    Returns:
        dict: {"date": str, "sent": bool}
    """
    from core.report import build_report
    from utils.config_loader import load_config
    from utils.notifier import build_notifier

    date = report_data.get("date", "")
    logger.info(f"report_task 시작 | date={date}")

    try:
        config   = load_config()
        notifier = build_notifier(config.notify)

        html_body = build_report(report_data)
        subject   = f"[NBT] 일일 분석 리포트 {date}"

        notifier.send_report(subject=subject, html_body=html_body)

        logger.info(f"report_task 완료 | date={date}")
        return {"date": date, "sent": True}

    except Exception as e:
        logger.error(f"report_task 예외 발생: {e}", exc_info=True)
        return {"date": date, "sent": False}