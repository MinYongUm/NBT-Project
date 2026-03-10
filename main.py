"""
NBT (Network Backup Tools)
Cisco 네트워크 장비 자동 백업 도구 - v3.1

사용법:
    python main.py backup                   # 전체 백업
    python main.py backup --group mgmt      # 특정 그룹만
    python main.py backup --dry-run         # 설정 검증만 (접속 X)
    python main.py history                  # 최근 백업 이력 조회
    python main.py history --limit 10       # 최근 10회 이력
    python main.py diff                     # 최근 Config Diff 조회
    python main.py diff --limit 20          # 최근 20건 Diff

지원 장비:
    - Cisco IOS / IOS-XE (mgmt)
    - Cisco NX-OS (nexus)
    - Cisco ACI (aci)
"""

import os
from pathlib import Path
from typing import Optional

import typer

app = typer.Typer(
    name="nbt",
    help="NBT (Network Backup Tools) — Cisco 장비 자동 백업",
    add_completion=False,
)

_BANNER = """
    ╔═══════════════════════════════════════════════════════════╗
    ║                                                           ║
    ║     NBT (Network Backup Tools) v3.1                       ║
    ║     Cisco Network Device Backup Automation                ║
    ║                                                           ║
    ╚═══════════════════════════════════════════════════════════╝
"""


# ------------------------------------------------------------------
# backup 커맨드
# ------------------------------------------------------------------

@app.command()
def backup(
    group: Optional[str] = typer.Option(
        None,
        "--group", "-g",
        help="실행할 장비 그룹 (mgmt / nexus / aci). 미입력 시 전체 실행.",
    ),
    dry_run: bool = typer.Option(
        False,
        "--dry-run",
        help="설정 검증만 수행합니다. 실제 장비 접속은 하지 않습니다.",
    ),
) -> None:
    """Cisco 네트워크 장비 설정 백업을 실행합니다."""
    typer.echo(_BANNER)

    from core.backup import run_backup

    if dry_run:
        typer.echo("  [DRY-RUN] 모드로 실행합니다.\n")

    if group:
        typer.echo(f"  그룹 필터: {group}\n")

    try:
        run_backup(group_filter=group, dry_run=dry_run)
    except FileNotFoundError as e:
        typer.echo(f"\n  [ERROR] 설정 파일을 찾을 수 없습니다:\n  {e}", err=True)
        raise typer.Exit(code=1)
    except EnvironmentError as e:
        typer.echo(f"\n  [ERROR] 환경변수 설정 오류:\n  {e}", err=True)
        raise typer.Exit(code=1)
    except ValueError as e:
        typer.echo(f"\n  [ERROR] 설정 파일 오류:\n  {e}", err=True)
        raise typer.Exit(code=1)
    except Exception as e:
        typer.echo(f"\n  [ERROR] 예기치 않은 오류: {e}", err=True)
        raise typer.Exit(code=1)


# ------------------------------------------------------------------
# history 커맨드
# ------------------------------------------------------------------

@app.command()
def history(
    limit: int = typer.Option(
        5,
        "--limit", "-n",
        help="조회할 실행 횟수 (기본: 5)",
        min=1,
        max=100,
    ),
) -> None:
    """최근 백업 실행 이력을 조회합니다."""
    from utils.db_manager import DBManager

    db_path = _get_db_path()
    if not db_path.exists():
        typer.echo("  백업 이력이 없습니다. 아직 backup 커맨드를 실행하지 않았거나 DB 파일이 없습니다.")
        raise typer.Exit(code=0)

    db = DBManager(db_path)
    db.initialize()

    try:
        runs = db.get_recent_runs(limit)
        if not runs:
            typer.echo("  백업 이력이 없습니다.")
            return

        typer.echo(f"\n  최근 백업 이력 (최대 {limit}회)")
        typer.echo(f"  {'─' * 70}")

        for run in runs:
            run_at = run["run_at"][:19].replace("T", " ")
            typer.echo(
                f"\n  RUN #{run['run_id']:<4} | {run_at} | "
                f"전체: {run['total']}  성공: {run['success']}  실패: {run['fail']}"
            )

            results = db.get_run_results(run["run_id"])
            for r in results:
                status = r["status"]
                icon = "OK  " if status == "SUCCESS" else "FAIL"
                hostname = (r["hostname"] or r["ip"])[:20]
                duration = f"{r['duration_sec']:.1f}s" if r["duration_sec"] else "-"
                error = f"  → {r['error_msg']}" if r["error_msg"] else ""
                typer.echo(
                    f"    [{icon}] {hostname:<20} {r['ip']:<16} "
                    f"{(r['device_type'] or ''):<16} {duration}{error}"
                )

        typer.echo(f"\n  {'─' * 70}\n")

    finally:
        db.close()


# ------------------------------------------------------------------
# diff 커맨드
# ------------------------------------------------------------------

@app.command()
def diff(
    limit: int = typer.Option(
        10,
        "--limit", "-n",
        help="조회할 Diff 건수 (기본: 10)",
        min=1,
        max=100,
    ),
) -> None:
    """최근 Config Diff 감지 이력을 조회합니다."""
    from utils.db_manager import DBManager

    db_path = _get_db_path()
    if not db_path.exists():
        typer.echo("  Diff 이력이 없습니다. 아직 backup 커맨드를 실행하지 않았거나 DB 파일이 없습니다.")
        raise typer.Exit(code=0)

    db = DBManager(db_path)
    db.initialize()

    try:
        diffs = db.get_recent_diffs(limit)
        if not diffs:
            typer.echo(f"\n  최근 {limit}건 내 Config 변경 없음.\n")
            return

        typer.echo(f"\n  최근 Config Diff 이력 (최대 {limit}건)")
        typer.echo(f"  {'─' * 70}")

        for d in diffs:
            detected_at = d["detected_at"][:19].replace("T", " ")
            prev_label = Path(d["previous_file"]).parent.name + "/" + Path(d["previous_file"]).name
            curr_label = Path(d["current_file"]).parent.name + "/" + Path(d["current_file"]).name
            typer.echo(f"\n  [{detected_at}]  {d['hostname']} ({d['ip']})")
            typer.echo(f"    변경 라인: {d['diff_lines']}개")
            typer.echo(f"    이전 파일: {prev_label}")
            typer.echo(f"    현재 파일: {curr_label}")

        typer.echo(f"\n  {'─' * 70}\n")

    finally:
        db.close()


# ------------------------------------------------------------------
# Helper
# ------------------------------------------------------------------

def _get_db_path() -> Path:
    """NBT_BACKUP_ROOT 환경변수에서 DB 경로를 결정합니다."""
    backup_root = os.environ.get("NBT_BACKUP_ROOT", "/data/backup")
    return Path(backup_root) / "nbt_history.db"


# ------------------------------------------------------------------
# Entry point
# ------------------------------------------------------------------

if __name__ == "__main__":
    app()