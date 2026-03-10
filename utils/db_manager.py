"""
NBT (Network Backup Tools) - Database Manager
Version: 3.0

SQLite 기반 백업 이력 및 Config Diff 저장 모듈.
thread-safe 설계: threading.Lock으로 모든 write 연산 직렬화.

Update History:
- ver 2.2: 최초 작성 (backup_runs, backup_results 테이블)
- ver 2.3: threading.Lock 추가 (ThreadPoolExecutor 병렬 실행 대응)
- ver 3.0: config_diffs 테이블 추가, get_last_backup_path() / save_diff() 추가
"""

import sqlite3
import threading
import logging
from datetime import datetime, timezone, timedelta
from pathlib import Path

logger = logging.getLogger(__name__)

KST = timezone(timedelta(hours=9))


def _now_kst() -> str:
    """현재 시각을 KST ISO 8601 문자열로 반환합니다."""
    return datetime.now(KST).isoformat()


class DBManager:
    """SQLite 백업 이력 및 Config Diff 관리 클래스."""

    def __init__(self, db_path: Path) -> None:
        """
        Args:
            db_path: SQLite DB 파일 경로.
                     부모 디렉토리가 존재하지 않으면 자동 생성.
        """
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._db_path = db_path
        self._lock = threading.Lock()
        self._conn: sqlite3.Connection | None = None

    # ------------------------------------------------------------------
    # 초기화
    # ------------------------------------------------------------------

    def initialize(self) -> None:
        """DB 연결 및 테이블 생성 (없을 경우)."""
        self._conn = sqlite3.connect(
            str(self._db_path),
            check_same_thread=False,
        )
        self._conn.row_factory = sqlite3.Row
        self._create_tables()
        logger.info("DB 초기화 완료: %s", self._db_path)

    def _create_tables(self) -> None:
        """필요한 테이블을 생성합니다 (IF NOT EXISTS)."""
        with self._lock:
            cursor = self._conn.cursor()

            cursor.executescript("""
                CREATE TABLE IF NOT EXISTS backup_runs (
                    run_id      INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_at      TEXT NOT NULL,
                    total       INTEGER DEFAULT 0,
                    success     INTEGER DEFAULT 0,
                    fail        INTEGER DEFAULT 0
                );

                CREATE TABLE IF NOT EXISTS backup_results (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id      INTEGER NOT NULL,
                    hostname    TEXT,
                    ip          TEXT,
                    device_type TEXT,
                    os_type     TEXT,
                    status      TEXT,
                    file_path   TEXT,
                    duration_sec REAL,
                    error_msg   TEXT,
                    backed_up_at TEXT,
                    FOREIGN KEY (run_id) REFERENCES backup_runs(run_id)
                );

                CREATE TABLE IF NOT EXISTS config_diffs (
                    id              INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id          INTEGER NOT NULL,
                    hostname        TEXT NOT NULL,
                    ip              TEXT,
                    previous_file   TEXT,
                    current_file    TEXT NOT NULL,
                    diff_lines      INTEGER DEFAULT 0,
                    diff_content    TEXT,
                    detected_at     TEXT NOT NULL,
                    FOREIGN KEY (run_id) REFERENCES backup_runs(run_id)
                );
            """)

            self._conn.commit()

    # ------------------------------------------------------------------
    # backup_runs
    # ------------------------------------------------------------------

    def start_run(self) -> int:
        """새 백업 실행 레코드를 생성하고 run_id를 반환합니다."""
        with self._lock:
            cursor = self._conn.cursor()
            cursor.execute(
                "INSERT INTO backup_runs (run_at) VALUES (?)",
                (_now_kst(),),
            )
            self._conn.commit()
            run_id = cursor.lastrowid
            logger.info("백업 실행 시작 (run_id=%d)", run_id)
            return run_id

    def finish_run(self, run_id: int, total: int, success: int, fail: int) -> None:
        """백업 실행 집계를 업데이트합니다."""
        with self._lock:
            self._conn.execute(
                """
                UPDATE backup_runs
                SET total = ?, success = ?, fail = ?
                WHERE run_id = ?
                """,
                (total, success, fail, run_id),
            )
            self._conn.commit()
        logger.info(
            "백업 실행 종료 (run_id=%d) | 전체: %d  성공: %d  실패: %d",
            run_id, total, success, fail,
        )

    # ------------------------------------------------------------------
    # backup_results
    # ------------------------------------------------------------------

    def save_result(
        self,
        run_id: int,
        hostname: str,
        ip: str,
        device_type: str,
        os_type: str,
        status: str,
        file_path: str,
        duration_sec: float,
        error_msg: str = "",
    ) -> None:
        """장비 단위 백업 결과를 저장합니다."""
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO backup_results
                    (run_id, hostname, ip, device_type, os_type,
                     status, file_path, duration_sec, error_msg, backed_up_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id, hostname, ip, device_type, os_type,
                    status, file_path, duration_sec, error_msg, _now_kst(),
                ),
            )
            self._conn.commit()

    # ------------------------------------------------------------------
    # config_diffs
    # ------------------------------------------------------------------

    def get_last_backup_path(self, hostname: str, current_run_id: int) -> str | None:
        """
        현재 run을 제외한 가장 최근 성공 백업의 파일 경로를 반환합니다.

        Args:
            hostname: 조회할 장비 hostname.
            current_run_id: 현재 실행 run_id (비교 대상에서 제외).

        Returns:
            이전 백업 파일 경로 문자열. 없으면 None.
        """
        cursor = self._conn.cursor()
        cursor.execute(
            """
            SELECT file_path
            FROM backup_results
            WHERE hostname = ?
              AND status = 'SUCCESS'
              AND run_id != ?
            ORDER BY run_id DESC
            LIMIT 1
            """,
            (hostname, current_run_id),
        )
        row = cursor.fetchone()
        return row["file_path"] if row else None

    def save_diff(
        self,
        run_id: int,
        hostname: str,
        ip: str,
        previous_file: str,
        current_file: str,
        diff_lines: int,
        diff_content: str,
    ) -> None:
        """Config Diff 결과를 저장합니다."""
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO config_diffs
                    (run_id, hostname, ip, previous_file, current_file,
                     diff_lines, diff_content, detected_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id, hostname, ip, previous_file, current_file,
                    diff_lines, diff_content, _now_kst(),
                ),
            )
            self._conn.commit()
        logger.info(
            "[%s] Config Diff 저장 완료 (변경 라인: %d)", hostname, diff_lines
        )

    # ------------------------------------------------------------------
    # 종료
    # ------------------------------------------------------------------

    def close(self) -> None:
        """DB 연결을 닫습니다."""
        if self._conn:
            self._conn.close()
            self._conn = None
            logger.info("DB 연결 종료")