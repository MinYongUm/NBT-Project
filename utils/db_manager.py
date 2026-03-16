"""
NBT (Network Backup Tools) - Database Manager
- SQLite 기반 백업 이력 및 Config Diff 저장 모듈
- threading.Lock으로 thread-safe write 보장
- WAL 모드 적용: Celery 다중 워커 환경에서 동시 read/write 충돌 방지 (v4.2)
- DB 파일 위치: {NBT_BACKUP_ROOT}/nbt_history.db

테이블:
    - backup_runs    : 실행 단위 기록
    - backup_results : 장비 단위 기록
    - config_diffs   : Config Diff 결과
    - devices        : 장비 목록 (v4.1)
"""

import logging
import sqlite3
import threading
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# KST = UTC+9
KST = timezone(timedelta(hours=9))


def _now_kst() -> str:
    """현재 시각을 KST ISO 8601 문자열로 반환합니다."""
    return datetime.now(KST).isoformat()


class DBManager:
    """SQLite 백업 이력 관리 클래스.

    단일 Connection을 다수 스레드가 공유합니다.
    모든 write 연산은 threading.Lock으로 직렬화됩니다.

    WAL(Write-Ahead Logging) 모드:
        - 기본 DELETE 모드: write 중 read도 블로킹 (Half-duplex)
        - WAL 모드: read/write 동시 허용 (Full-duplex)
        - Celery 워커가 backup_results에 write하는 동안
          FastAPI가 history API로 read하는 상황에서 충돌 방지
    """

    def __init__(self, db_path: Path) -> None:
        self._db_path = db_path
        self._lock = threading.Lock()
        self._conn: Optional[sqlite3.Connection] = None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def initialize(self) -> None:
        """DB 파일을 열고 WAL 모드 설정 후 테이블을 생성합니다."""
        self._conn = sqlite3.connect(
            str(self._db_path),
            check_same_thread=False,
        )
        self._conn.row_factory = sqlite3.Row

        # WAL 모드: 동시 read/write 허용 (Celery 다중 워커 대응)
        self._conn.execute("PRAGMA journal_mode=WAL")
        # WAL 모드에서 안전한 동기화 수준 — fsync 최소화로 I/O 성능 향상
        self._conn.execute("PRAGMA synchronous=NORMAL")

        self._create_tables()
        logger.info(f"DB 초기화 완료 (WAL 모드): {self._db_path}")

    def close(self) -> None:
        """DB 연결을 닫습니다."""
        if self._conn:
            self._conn.close()
            self._conn = None
            logger.info("DB 연결 종료")

    def _create_tables(self) -> None:
        with self._lock:
            self._conn.executescript("""
                CREATE TABLE IF NOT EXISTS backup_runs (
                    run_id      INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_at      TEXT NOT NULL,
                    finished_at TEXT,
                    total       INTEGER DEFAULT 0,
                    success     INTEGER DEFAULT 0,
                    fail        INTEGER DEFAULT 0
                );

                CREATE TABLE IF NOT EXISTS backup_results (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id      INTEGER NOT NULL REFERENCES backup_runs(run_id),
                    hostname    TEXT,
                    ip          TEXT NOT NULL,
                    device_type TEXT,
                    os_type     TEXT,
                    status      TEXT NOT NULL,
                    file_path   TEXT,
                    duration_sec REAL,
                    error_msg   TEXT,
                    backed_up_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS config_diffs (
                    id            INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id        INTEGER NOT NULL REFERENCES backup_runs(run_id),
                    hostname      TEXT NOT NULL,
                    ip            TEXT NOT NULL,
                    previous_file TEXT,
                    current_file  TEXT,
                    diff_lines    INTEGER DEFAULT 0,
                    diff_content  TEXT,
                    detected_at   TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS devices (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    group_name  TEXT NOT NULL,
                    device_type TEXT NOT NULL,
                    ip          TEXT NOT NULL UNIQUE,
                    description TEXT,
                    is_active   INTEGER DEFAULT 1,
                    created_at  TEXT NOT NULL,
                    updated_at  TEXT NOT NULL
                );
            """)
            self._conn.commit()

    # ------------------------------------------------------------------
    # Write API — backup_runs
    # ------------------------------------------------------------------

    def start_run(self) -> int:
        """새로운 백업 실행을 기록하고 run_id를 반환합니다."""
        with self._lock:
            cur = self._conn.execute(
                "INSERT INTO backup_runs (run_at) VALUES (?)",
                (_now_kst(),),
            )
            self._conn.commit()
            run_id = cur.lastrowid
            logger.info(f"백업 실행 시작 기록: run_id={run_id}")
            return run_id

    def finish_run(self, run_id: int, total: int, success: int, fail: int) -> None:
        """백업 실행 완료를 기록합니다."""
        with self._lock:
            self._conn.execute(
                """UPDATE backup_runs
                   SET finished_at = ?, total = ?, success = ?, fail = ?
                   WHERE run_id = ?""",
                (_now_kst(), total, success, fail, run_id),
            )
            self._conn.commit()
            logger.info(
                f"백업 실행 완료 기록: run_id={run_id} "
                f"전체={total} 성공={success} 실패={fail}"
            )

    def save_result(
        self,
        run_id: int,
        hostname: str,
        ip: str,
        device_type: str,
        os_type: str,
        status: str,
        file_path: Optional[str],
        duration_sec: float,
        error_msg: Optional[str],
    ) -> None:
        """장비 단위 백업 결과를 저장합니다."""
        with self._lock:
            self._conn.execute(
                """INSERT INTO backup_results
                   (run_id, hostname, ip, device_type, os_type,
                    status, file_path, duration_sec, error_msg, backed_up_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    run_id, hostname, ip, device_type, os_type,
                    status, file_path, duration_sec, error_msg, _now_kst(),
                ),
            )
            self._conn.commit()

    def get_last_backup_path(
        self, hostname: str, current_run_id: int
    ) -> Optional[str]:
        """이전 성공 백업 파일 경로를 반환합니다.

        현재 run을 제외하고 SUCCESS 상태 기준 가장 최근 경로를 반환합니다.
        """
        row = self._conn.execute(
            """SELECT file_path FROM backup_results
               WHERE hostname = ?
                 AND status   = 'SUCCESS'
                 AND run_id   < ?
                 AND file_path IS NOT NULL
               ORDER BY backed_up_at DESC
               LIMIT 1""",
            (hostname, current_run_id),
        ).fetchone()
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
                """INSERT INTO config_diffs
                   (run_id, hostname, ip, previous_file, current_file,
                    diff_lines, diff_content, detected_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    run_id, hostname, ip, previous_file, current_file,
                    diff_lines, diff_content, _now_kst(),
                ),
            )
            self._conn.commit()
            logger.info(f"Config Diff 저장: {hostname} ({diff_lines}줄 변경)")

    # ------------------------------------------------------------------
    # Read API (CLI history / diff 커맨드용)
    # ------------------------------------------------------------------

    def get_recent_runs(self, limit: int = 5) -> list:
        """최근 백업 실행 이력을 반환합니다."""
        rows = self._conn.execute(
            """SELECT run_id, run_at, finished_at, total, success, fail
               FROM backup_runs
               ORDER BY run_id DESC
               LIMIT ?""",
            (limit,),
        ).fetchall()
        return rows

    def get_run_results(self, run_id: int) -> list:
        """특정 run의 장비별 결과를 반환합니다."""
        rows = self._conn.execute(
            """SELECT hostname, ip, device_type, status, duration_sec, error_msg
               FROM backup_results
               WHERE run_id = ?
               ORDER BY backed_up_at ASC""",
            (run_id,),
        ).fetchall()
        return rows

    def get_recent_diffs(self, limit: int = 10) -> list:
        """최근 Config Diff 이력을 반환합니다."""
        rows = self._conn.execute(
            """SELECT hostname, ip, diff_lines, previous_file,
                      current_file, detected_at
               FROM config_diffs
               ORDER BY detected_at DESC
               LIMIT ?""",
            (limit,),
        ).fetchall()
        return rows

    # ------------------------------------------------------------------
    # Devices CRUD (v4.1)
    # ------------------------------------------------------------------

    def get_all_devices(self, include_inactive: bool = False) -> list:
        """장비 목록을 반환합니다."""
        if include_inactive:
            rows = self._conn.execute(
                "SELECT * FROM devices ORDER BY group_name, ip"
            ).fetchall()
        else:
            rows = self._conn.execute(
                "SELECT * FROM devices WHERE is_active = 1 ORDER BY group_name, ip"
            ).fetchall()
        return rows

    def get_device(self, device_id: int) -> Optional[sqlite3.Row]:
        """단일 장비를 반환합니다."""
        return self._conn.execute(
            "SELECT * FROM devices WHERE id = ?", (device_id,)
        ).fetchone()

    def add_device(
        self,
        group_name: str,
        device_type: str,
        ip: str,
        description: str = "",
    ) -> int:
        """장비를 추가합니다.

        - 신규 IP: INSERT
        - is_active=0인 IP: UPDATE로 재활성화 (group_name, device_type, description 갱신)
        - is_active=1인 IP: IntegrityError 발생 → 호출 측에서 409 처리
        """
        with self._lock:
            # 비활성화된 IP 확인 후 재활성화
            existing = self._conn.execute(
                "SELECT id, is_active FROM devices WHERE ip = ?", (ip,)
            ).fetchone()

            now = _now_kst()

            if existing and existing["is_active"] == 0:
                self._conn.execute(
                    """UPDATE devices
                       SET group_name = ?, device_type = ?, description = ?,
                           is_active = 1, updated_at = ?
                       WHERE ip = ?""",
                    (group_name, device_type, description, now, ip),
                )
                self._conn.commit()
                row = self._conn.execute(
                    "SELECT id FROM devices WHERE ip = ?", (ip,)
                ).fetchone()
                logger.info(f"장비 재활성화: {ip} (id={row['id']})")
                return row["id"]

            # 신규 추가 (is_active=1인 IP는 UNIQUE 제약으로 IntegrityError 발생)
            cur = self._conn.execute(
                """INSERT INTO devices
                   (group_name, device_type, ip, description, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (group_name, device_type, ip, description, now, now),
            )
            self._conn.commit()
            logger.info(f"장비 추가: {ip} (id={cur.lastrowid})")
            return cur.lastrowid

    def update_device(
        self,
        device_id: int,
        group_name: str,
        device_type: str,
        ip: str,
        description: str = "",
    ) -> bool:
        """장비 정보를 수정합니다. 존재하지 않으면 False를 반환합니다."""
        with self._lock:
            cur = self._conn.execute(
                """UPDATE devices
                   SET group_name = ?, device_type = ?, ip = ?,
                       description = ?, updated_at = ?
                   WHERE id = ? AND is_active = 1""",
                (group_name, device_type, ip, description, _now_kst(), device_id),
            )
            self._conn.commit()
            return cur.rowcount > 0

    def deactivate_device(self, device_id: int) -> bool:
        """장비를 비활성화합니다 (소프트 삭제). 존재하지 않으면 False를 반환합니다."""
        with self._lock:
            cur = self._conn.execute(
                """UPDATE devices
                   SET is_active = 0, updated_at = ?
                   WHERE id = ? AND is_active = 1""",
                (_now_kst(), device_id),
            )
            self._conn.commit()
            return cur.rowcount > 0

    def upsert_devices_from_yaml(self, settings_path: Path) -> int:
        """settings.yaml의 장비 목록을 devices 테이블로 마이그레이션합니다.

        이미 존재하는 IP는 건너뜁니다 (중복 무시).
        Returns:
            int: 새로 추가된 장비 수
        """
        import yaml

        if not settings_path.exists():
            logger.warning(f"settings.yaml 없음 — 장비 마이그레이션 건너뜀: {settings_path}")
            return 0

        with settings_path.open(encoding="utf-8") as f:
            settings = yaml.safe_load(f) or {}

        devices_raw = settings.get("devices", {})
        if not devices_raw:
            return 0

        count = 0
        for group_name, group_data in devices_raw.items():
            device_type = group_data.get("device_type", "")
            hosts = group_data.get("hosts", [])
            for ip in hosts:
                try:
                    self.add_device(group_name, device_type, ip)
                    count += 1
                except sqlite3.IntegrityError:
                    # 이미 활성 상태로 존재 — 건너뜀
                    pass

        logger.info(f"settings.yaml 마이그레이션 완료: {count}대 추가")
        return count