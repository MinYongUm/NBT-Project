"""
NBT (Network Backup Tools) - Database Manager
- SQLite 기반 백업 이력 및 Config Diff 저장 모듈
- threading.Lock으로 thread-safe write 보장
- DB 파일 위치: {NBT_BACKUP_ROOT}/nbt_history.db

테이블:
    - backup_runs    : 실행 단위 기록
    - backup_results : 장비 단위 기록
    - config_diffs   : Config Diff 결과
    - devices        : 장비 목록 (v4.1 추가)
"""

import logging
import sqlite3
import threading
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional

import yaml

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
    """

    def __init__(self, db_path: Path) -> None:
        self._db_path = db_path
        self._lock = threading.Lock()
        self._conn: Optional[sqlite3.Connection] = None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def initialize(self) -> None:
        """DB 파일을 열고 테이블을 생성합니다."""
        self._conn = sqlite3.connect(
            str(self._db_path),
            check_same_thread=False,
        )
        self._conn.row_factory = sqlite3.Row
        self._create_tables()
        logger.info(f"DB 초기화 완료: {self._db_path}")

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
    # Write API — backup
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
        """이전 성공 백업 파일 경로를 반환합니다."""
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
    # Read API — CLI history / diff 커맨드용
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
                      current_file, detected_at, diff_content
               FROM config_diffs
               ORDER BY detected_at DESC
               LIMIT ?""",
            (limit,),
        ).fetchall()
        return rows

    # ------------------------------------------------------------------
    # CRUD API — devices (v4.1)
    # ------------------------------------------------------------------

    def get_all_devices(self, include_inactive: bool = False) -> list:
        """장비 목록을 반환합니다.

        Args:
            include_inactive: True이면 비활성 장비도 포함합니다.

        Returns:
            list[sqlite3.Row]: id, group_name, device_type, ip,
                               description, is_active, created_at, updated_at
        """
        if include_inactive:
            rows = self._conn.execute(
                """SELECT id, group_name, device_type, ip, description,
                          is_active, created_at, updated_at
                   FROM devices
                   ORDER BY group_name, ip"""
            ).fetchall()
        else:
            rows = self._conn.execute(
                """SELECT id, group_name, device_type, ip, description,
                          is_active, created_at, updated_at
                   FROM devices
                   WHERE is_active = 1
                   ORDER BY group_name, ip"""
            ).fetchall()
        return rows

    def get_device(self, device_id: int) -> Optional[sqlite3.Row]:
        """단일 장비를 반환합니다.

        Args:
            device_id: devices.id

        Returns:
            sqlite3.Row | None
        """
        row = self._conn.execute(
            """SELECT id, group_name, device_type, ip, description,
                      is_active, created_at, updated_at
               FROM devices
               WHERE id = ?""",
            (device_id,),
        ).fetchone()
        return row

    def add_device(
        self,
        group_name: str,
        device_type: str,
        ip: str,
        description: str = "",
    ) -> int:
        """새 장비를 추가합니다.

        동일 IP가 비활성(is_active=0) 상태로 존재하면 재활성화합니다.
        활성(is_active=1) 상태로 이미 존재하면 IntegrityError를 발생시킵니다.

        Args:
            group_name:  장비 그룹 (mgmt / nexus / aci)
            device_type: Netmiko device_type (cisco_ios / cisco_nxos)
            ip:          장비 IP 주소 (UNIQUE)
            description: 장비 설명 메모 (선택)

        Returns:
            int: 생성 또는 재활성화된 장비의 id

        Raises:
            sqlite3.IntegrityError: 활성 상태의 ip가 이미 존재할 때
        """
        now = _now_kst()
        with self._lock:
            # 동일 IP가 비활성 상태로 존재하는지 먼저 확인
            existing = self._conn.execute(
                "SELECT id, is_active FROM devices WHERE ip = ?",
                (ip,),
            ).fetchone()

            if existing:
                if existing["is_active"] == 0:
                    # 비활성 장비 → 정보 업데이트 후 재활성화
                    self._conn.execute(
                        """UPDATE devices
                           SET group_name  = ?,
                               device_type = ?,
                               description = ?,
                               is_active   = 1,
                               updated_at  = ?
                           WHERE id = ?""",
                        (group_name, device_type, description, now, existing["id"]),
                    )
                    self._conn.commit()
                    device_id = existing["id"]
                    logger.info(f"장비 재활성화: id={device_id} ip={ip} group={group_name}")
                    return device_id
                else:
                    # 활성 상태로 이미 존재 → 중복 오류
                    raise sqlite3.IntegrityError(
                        f"UNIQUE constraint failed: devices.ip ({ip})"
                    )

            # 신규 INSERT
            cur = self._conn.execute(
                """INSERT INTO devices
                   (group_name, device_type, ip, description, is_active,
                    created_at, updated_at)
                   VALUES (?, ?, ?, ?, 1, ?, ?)""",
                (group_name, device_type, ip, description, now, now),
            )
            self._conn.commit()
            device_id = cur.lastrowid
            logger.info(f"장비 추가: id={device_id} ip={ip} group={group_name}")
            return device_id

    def update_device(
        self,
        device_id: int,
        group_name: str,
        device_type: str,
        ip: str,
        description: str = "",
    ) -> bool:
        """장비 정보를 수정합니다.

        Args:
            device_id:   수정할 장비의 id
            group_name:  변경할 그룹명
            device_type: 변경할 device_type
            ip:          변경할 IP (UNIQUE 제약 적용)
            description: 변경할 설명

        Returns:
            bool: 수정된 행이 있으면 True, 없으면 False
        """
        with self._lock:
            cur = self._conn.execute(
                """UPDATE devices
                   SET group_name  = ?,
                       device_type = ?,
                       ip          = ?,
                       description = ?,
                       updated_at  = ?
                   WHERE id = ?""",
                (group_name, device_type, ip, description, _now_kst(), device_id),
            )
            self._conn.commit()
            updated = cur.rowcount > 0
            if updated:
                logger.info(f"장비 수정: id={device_id} ip={ip}")
            else:
                logger.warning(f"장비 수정 대상 없음: id={device_id}")
            return updated

    def deactivate_device(self, device_id: int) -> bool:
        """장비를 비활성화합니다 (소프트 삭제).

        실제 행을 삭제하지 않고 is_active=0으로 변경합니다.
        백업 이력(backup_results)과의 연결을 유지하기 위함입니다.

        Args:
            device_id: 비활성화할 장비의 id

        Returns:
            bool: 처리된 행이 있으면 True, 없으면 False
        """
        with self._lock:
            cur = self._conn.execute(
                """UPDATE devices
                   SET is_active  = 0,
                       updated_at = ?
                   WHERE id = ?""",
                (_now_kst(), device_id),
            )
            self._conn.commit()
            updated = cur.rowcount > 0
            if updated:
                logger.info(f"장비 비활성화: id={device_id}")
            else:
                logger.warning(f"장비 비활성화 대상 없음: id={device_id}")
            return updated

    def upsert_devices_from_yaml(self, settings_path: Path) -> int:
        """settings.yaml의 devices 섹션을 DB로 import합니다 (A안 마이그레이션).

        이미 DB에 있는 IP는 건너뜁니다 (INSERT OR IGNORE).
        서버 시작 시 1회 호출하여 초기 데이터를 투입합니다.

        Args:
            settings_path: config/settings.yaml 경로

        Returns:
            int: 새로 추가된 장비 수

        Raises:
            FileNotFoundError: settings.yaml이 없는 경우
        """
        if not settings_path.exists():
            raise FileNotFoundError(
                f"settings.yaml을 찾을 수 없습니다: {settings_path}"
            )

        with settings_path.open(encoding="utf-8") as f:
            settings: dict = yaml.safe_load(f) or {}

        devices_raw: dict = settings.get("devices", {})
        if not devices_raw:
            logger.warning("settings.yaml에 devices 섹션 없음 — 마이그레이션 건너뜀")
            return 0

        now = _now_kst()
        inserted = 0

        with self._lock:
            for group_name, group_data in devices_raw.items():
                device_type = group_data.get("device_type", "")
                hosts: list = group_data.get("hosts", [])

                for ip in hosts:
                    cur = self._conn.execute(
                        """INSERT OR IGNORE INTO devices
                           (group_name, device_type, ip, description,
                            is_active, created_at, updated_at)
                           VALUES (?, ?, ?, '', 1, ?, ?)""",
                        (group_name, device_type, ip, now, now),
                    )
                    if cur.rowcount > 0:
                        inserted += 1
                        logger.info(
                            f"장비 import: ip={ip} group={group_name}"
                        )

            self._conn.commit()

        logger.info(
            f"settings.yaml 마이그레이션 완료 — 신규 추가: {inserted}대"
        )
        return inserted