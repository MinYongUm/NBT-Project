"""
NBT (Network Backup Tools) - Database Manager
Version: 2.2

백업 실행 결과를 SQLite DB에 저장하는 모듈.

테이블 구조:
    backup_runs    : 실행 단위 기록 (1회 실행 = 1 row)
    backup_results : 장비 단위 기록 (1개 장비 = 1 row, backup_runs와 1:N)
"""

import sqlite3
import logging
from datetime import datetime, timezone, timedelta
from pathlib import Path

logger = logging.getLogger(__name__)

# 한국 표준시 (UTC+9)
KST = timezone(timedelta(hours=9))


def _now_kst() -> str:
    """현재 시각을 KST ISO 8601 문자열로 반환합니다.

    Returns:
        str: 예) '2026-03-10T14:30:00+09:00'
    """
    return datetime.now(KST).isoformat(timespec='seconds')


class DBManager:
    """SQLite 백업 이력 관리 클래스.

    사용 예:
        db = DBManager(db_path)
        db.initialize()
        run_id = db.start_run()
        db.save_result(run_id, ...)
        db.finish_run(run_id, total=3, success=2, fail=1)
        db.close()
    """

    def __init__(self, db_path: Path) -> None:
        """
        Args:
            db_path: SQLite DB 파일 경로 (예: /data/backup/nbt_history.db)
        """
        self.db_path = db_path
        self.conn: sqlite3.Connection | None = None

    # ------------------------------------------------------------------
    # 연결 관리
    # ------------------------------------------------------------------

    def connect(self) -> None:
        """DB에 연결합니다. 파일이 없으면 자동 생성됩니다."""
        self.conn = sqlite3.connect(str(self.db_path))
        # 쿼리 결과를 dict처럼 컬럼명으로 접근 가능하게 설정
        self.conn.row_factory = sqlite3.Row
        logger.info(f"DB 연결: {self.db_path}")

    def close(self) -> None:
        """DB 연결을 종료합니다."""
        if self.conn:
            self.conn.close()
            self.conn = None
            logger.info("DB 연결 종료.")

    # ------------------------------------------------------------------
    # 초기화 (테이블 생성)
    # ------------------------------------------------------------------

    def initialize(self) -> None:
        """DB에 연결하고 테이블이 없으면 생성합니다.

        이미 테이블이 존재하면 스킵합니다 (IF NOT EXISTS).
        """
        self.connect()
        self._create_tables()
        logger.info("DB 초기화 완료.")

    def _create_tables(self) -> None:
        """backup_runs, backup_results 테이블을 생성합니다."""
        assert self.conn is not None

        self.conn.executescript("""
            CREATE TABLE IF NOT EXISTS backup_runs (
                run_id      INTEGER PRIMARY KEY AUTOINCREMENT,
                run_at      TEXT    NOT NULL,
                total       INTEGER DEFAULT 0,
                success     INTEGER DEFAULT 0,
                fail        INTEGER DEFAULT 0
            );

            CREATE TABLE IF NOT EXISTS backup_results (
                result_id    INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id       INTEGER NOT NULL,
                hostname     TEXT,
                ip           TEXT    NOT NULL,
                device_type  TEXT    NOT NULL,
                os_type      TEXT,
                status       TEXT    NOT NULL CHECK(status IN ('SUCCESS', 'FAIL')),
                file_path    TEXT,
                duration_sec REAL,
                error_msg    TEXT,
                backed_up_at TEXT    NOT NULL,
                FOREIGN KEY (run_id) REFERENCES backup_runs(run_id)
            );
        """)
        self.conn.commit()

    # ------------------------------------------------------------------
    # 실행 단위 기록 (backup_runs)
    # ------------------------------------------------------------------

    def start_run(self) -> int:
        """백업 실행 시작을 기록하고 run_id를 반환합니다.

        Returns:
            int: 생성된 run_id (이후 save_result / finish_run에 사용)
        """
        assert self.conn is not None

        cursor = self.conn.execute(
            "INSERT INTO backup_runs (run_at) VALUES (?)",
            (_now_kst(),)
        )
        self.conn.commit()
        run_id = cursor.lastrowid
        logger.info(f"백업 실행 시작 기록. run_id={run_id}")
        return run_id

    def finish_run(self, run_id: int, total: int, success: int, fail: int) -> None:
        """백업 실행 종료 시 집계 결과를 업데이트합니다.

        Args:
            run_id:  start_run()이 반환한 run_id
            total:   전체 장비 수
            success: 성공한 장비 수
            fail:    실패한 장비 수
        """
        assert self.conn is not None

        self.conn.execute(
            """
            UPDATE backup_runs
            SET total = ?, success = ?, fail = ?
            WHERE run_id = ?
            """,
            (total, success, fail, run_id)
        )
        self.conn.commit()
        logger.info(
            f"백업 실행 종료 기록. run_id={run_id} "
            f"| 전체={total} 성공={success} 실패={fail}"
        )

    # ------------------------------------------------------------------
    # 장비 단위 기록 (backup_results)
    # ------------------------------------------------------------------

    def save_result(
        self,
        run_id:       int,
        hostname:     str | None,
        ip:           str,
        device_type:  str,
        os_type:      str | None,
        status:       str,
        file_path:    str | None = None,
        duration_sec: float | None = None,
        error_msg:    str | None = None,
    ) -> None:
        """장비 1대의 백업 결과를 저장합니다.

        Args:
            run_id:       start_run()이 반환한 run_id
            hostname:     장비 hostname (접속 실패 시 None 가능)
            ip:           장비 IP 주소
            device_type:  Netmiko device_type (예: 'cisco_ios')
            os_type:      장비 그룹명 (예: 'MGMT', 'NEXUS', 'ACI')
            status:       'SUCCESS' 또는 'FAIL'
            file_path:    백업 파일 경로 (실패 시 None)
            duration_sec: 백업 소요 시간(초) (실패 시 None)
            error_msg:    에러 메시지 (성공 시 None)
        """
        assert self.conn is not None

        self.conn.execute(
            """
            INSERT INTO backup_results
                (run_id, hostname, ip, device_type, os_type,
                 status, file_path, duration_sec, error_msg, backed_up_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                run_id, hostname, ip, device_type, os_type,
                status, file_path, duration_sec, error_msg, _now_kst()
            )
        )
        self.conn.commit()
        logger.info(
            f"장비 결과 저장. run_id={run_id} "
            f"ip={ip} hostname={hostname} status={status}"
        )