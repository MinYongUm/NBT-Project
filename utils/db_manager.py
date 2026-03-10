"""
NBT (Network Backup Tools) - Database Manager
Version: 2.3

백업 이력을 SQLite DB에 저장하는 모듈.
ThreadPoolExecutor 환경에서 thread-safe하게 동작하도록 threading.Lock 적용.

테이블 구조:
    backup_runs    : 실행 단위 기록 (run_id, run_at, total, success, fail)
    backup_results : 장비 단위 기록 (run_id FK, hostname, ip, ...)
"""

import sqlite3
import threading
import logging
from datetime import datetime, timezone, timedelta
from pathlib import Path


KST = timezone(timedelta(hours=9))


def _now_kst() -> str:
    """KST 기준 ISO 8601 타임스탬프를 반환합니다."""
    return datetime.now(KST).isoformat(timespec='seconds')


class DBManager:
    """
    SQLite 기반 백업 이력 관리 클래스.

    Thread-safe 설계:
        - check_same_thread=False : 단일 Connection을 다수 스레드가 공유 허용
        - self._lock (threading.Lock) : 모든 write 연산에 직렬화 적용
        - read 연산(close 제외)은 현재 없으므로 Lock으로 충분
    """

    def __init__(self, db_path: Path) -> None:
        self._db_path = db_path
        self._conn: sqlite3.Connection | None = None
        self._lock = threading.Lock()
        self._run_id: int | None = None

    # ------------------------------------------------------------------
    # 초기화
    # ------------------------------------------------------------------
    def initialize(self) -> None:
        """DB 연결 및 테이블 생성."""
        self._conn = sqlite3.connect(
            str(self._db_path),
            check_same_thread=False,   # 다수 스레드에서 단일 Connection 공유
        )
        self._conn.row_factory = sqlite3.Row
        self._create_tables()
        logging.info(f"[DB] 초기화 완료: {self._db_path}")

    def _create_tables(self) -> None:
        """backup_runs / backup_results 테이블 생성 (없을 경우에만)."""
        with self._lock:
            cursor = self._conn.cursor()
            cursor.executescript("""
                CREATE TABLE IF NOT EXISTS backup_runs (
                    run_id      INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_at      TEXT    NOT NULL,
                    total       INTEGER DEFAULT 0,
                    success     INTEGER DEFAULT 0,
                    fail        INTEGER DEFAULT 0
                );

                CREATE TABLE IF NOT EXISTS backup_results (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id      INTEGER NOT NULL REFERENCES backup_runs(run_id),
                    hostname    TEXT,
                    ip          TEXT    NOT NULL,
                    device_type TEXT,
                    os_type     TEXT,
                    status      TEXT    NOT NULL,   -- 'SUCCESS' | 'FAIL'
                    file_path   TEXT,
                    duration_sec REAL,
                    error_msg   TEXT,
                    backed_up_at TEXT   NOT NULL
                );
            """)
            self._conn.commit()

    # ------------------------------------------------------------------
    # 실행 단위 관리
    # ------------------------------------------------------------------
    def start_run(self) -> int:
        """
        새 백업 실행 레코드를 생성하고 run_id를 반환합니다.

        Returns:
            int: 생성된 run_id
        """
        with self._lock:
            cursor = self._conn.cursor()
            cursor.execute(
                "INSERT INTO backup_runs (run_at) VALUES (?)",
                (_now_kst(),)
            )
            self._conn.commit()
            self._run_id = cursor.lastrowid
            logging.info(f"[DB] 백업 실행 시작 — run_id: {self._run_id}")
            return self._run_id

    def finish_run(self, total: int, success: int, fail: int) -> None:
        """
        백업 실행 집계 결과를 업데이트합니다.

        Args:
            total   : 전체 장비 수
            success : 성공 장비 수
            fail    : 실패 장비 수
        """
        if self._run_id is None:
            logging.warning("[DB] finish_run 호출 전 start_run이 필요합니다.")
            return

        with self._lock:
            self._conn.execute(
                """
                UPDATE backup_runs
                SET total = ?, success = ?, fail = ?
                WHERE run_id = ?
                """,
                (total, success, fail, self._run_id)
            )
            self._conn.commit()
            logging.info(
                f"[DB] 백업 실행 종료 — run_id: {self._run_id} "
                f"| 전체: {total}  성공: {success}  실패: {fail}"
            )

    # ------------------------------------------------------------------
    # 장비 단위 저장
    # ------------------------------------------------------------------
    def save_result(
        self,
        hostname: str,
        ip: str,
        device_type: str,
        os_type: str,
        status: str,
        file_path: str | None = None,
        duration_sec: float | None = None,
        error_msg: str | None = None,
    ) -> None:
        """
        개별 장비 백업 결과를 저장합니다.

        Thread-safe: Lock으로 직렬화되므로 다수 스레드에서 동시 호출 안전.

        Args:
            hostname    : 장비 hostname (find_prompt 결과)
            ip          : 장비 IP
            device_type : Netmiko device_type (cisco_ios 등)
            os_type     : 장비 그룹명 (MGMT / NEXUS / ACI)
            status      : 'SUCCESS' 또는 'FAIL'
            file_path   : 저장된 백업 파일 경로
            duration_sec: 백업 소요 시간 (초)
            error_msg   : 오류 메시지 (실패 시)
        """
        if self._run_id is None:
            logging.warning("[DB] save_result 호출 전 start_run이 필요합니다.")
            return

        with self._lock:
            self._conn.execute(
                """
                INSERT INTO backup_results
                    (run_id, hostname, ip, device_type, os_type,
                     status, file_path, duration_sec, error_msg, backed_up_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    self._run_id,
                    hostname,
                    ip,
                    device_type,
                    os_type,
                    status,
                    file_path,
                    duration_sec,
                    error_msg,
                    _now_kst(),
                )
            )
            self._conn.commit()

    # ------------------------------------------------------------------
    # 종료
    # ------------------------------------------------------------------
    def close(self) -> None:
        """DB 연결을 닫습니다."""
        if self._conn:
            self._conn.close()
            self._conn = None
            logging.info("[DB] 연결 종료.")