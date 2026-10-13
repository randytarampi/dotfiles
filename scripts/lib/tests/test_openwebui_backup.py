"""Exercise pre-upgrade database backups without changing host services."""

from contextlib import closing
import os
from pathlib import Path
import shlex
import sqlite3
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[3]


def _run_backup(
    tmp_path, *, active=False, process_active=False, platform="darwin", failed_copy=""
):
    template = (ROOT / ".chezmoiscripts/run_onchange_30-openwebui.sh.tmpl").read_text()
    start = template.index('if [[ -n "$INSTALLED_VERSION"')
    end = template.index('if ! "$VENV_DIR/bin/python" -m pip install')
    home = tmp_path / "home"
    plist = home / "Library/LaunchAgents/com.openwebui.web.plist"
    plist.parent.mkdir(parents=True, exist_ok=True)
    plist.touch()
    venv = tmp_path / "venv"
    (venv / "bin").mkdir(parents=True, exist_ok=True)
    (venv / "bin/python").symlink_to(sys.executable)
    trace = tmp_path / "service.log"
    script = f"""
set -euo pipefail
source {shlex.quote(str(ROOT / 'scripts/lib/openwebui_service.sh'))}
info() {{ :; }}
ok() {{ printf '%s\\n' "$*"; }}
warn() {{ printf '%s\\n' "$*" >&2; }}
die() {{ printf '%s\\n' "$*" >&2; exit 1; }}
launchctl() {{
  printf '%s\\n' "$*" >> "$TRACE"
  [[ "$1" == print && "$ACTIVE" == 1 ]]
}}
systemctl() {{
  printf '%s\\n' "$*" >> "$TRACE"
  [[ "$2" == is-active && "$ACTIVE" == 1 ]]
}}
pgrep() {{ [[ "$PROCESS_ACTIVE" == 1 ]]; }}
cp() {{
  local source_path="$1"
  [[ "$source_path" != -p ]] || source_path="$2"
  [[ -z "$FAILED_COPY" || "$source_path" != *"$FAILED_COPY" ]] || return 1
  command cp "$@"
}}
{template[start:end]}
printf 'upgrade-reached\\n'
"""
    result = subprocess.run(
        ["bash", "-c", script],
        env={
            **os.environ,
            "HOME": str(home),
            "DATA_DIR": str(tmp_path / "data"),
            "VENV_DIR": str(venv),
            "INSTALLED_VERSION": "0.11.4",
            "VERSION": "0.12.0",
            "OS_NAME": platform,
            "ACTIVE": "1" if active else "0",
            "PROCESS_ACTIVE": "1" if process_active else "0",
            "FAILED_COPY": failed_copy,
            "TRACE": str(trace),
        },
        capture_output=True,
        text=True,
    )
    return result, trace.read_text().splitlines()


def _database(tmp_path):
    data = tmp_path / "data"
    data.mkdir()
    database = data / "webui.db"
    with closing(sqlite3.connect(database)) as conn:
        conn.execute("CREATE TABLE messages (text TEXT)")
        conn.execute("INSERT INTO messages VALUES ('before upgrade')")
        conn.commit()
    database.chmod(0o666)
    return database


def test_stopped_backup_secures_database_and_companions(tmp_path):
    database = _database(tmp_path)
    for suffix in ("-wal", "-shm"):
        companion = Path(str(database) + suffix)
        companion.write_bytes(b"companion fixture")
        companion.chmod(0o666)

    result, events = _run_backup(tmp_path)

    assert result.returncode == 0, result.stderr
    backups = sorted(database.parent.glob("webui.db.bak-*"))
    assert len(backups) == 3
    for backup in backups:
        suffix = (
            "-wal"
            if backup.name.endswith("-wal")
            else "-shm" if backup.name.endswith("-shm") else ""
        )
        assert backup.read_bytes() == Path(str(database) + suffix).read_bytes()
        assert backup.stat().st_mode & 0o777 == 0o600
    assert events == [
        f"bootout gui/{os.getuid()}/com.openwebui.web",
        f"print gui/{os.getuid()}/com.openwebui.web",
    ]


@pytest.mark.parametrize(
    "platform,active,process_active",
    [("darwin", True, False), ("linux", True, False), ("darwin", False, True)],
)
def test_active_backup_includes_committed_wal_without_copying_companions(
    tmp_path, platform, active, process_active
):
    database = _database(tmp_path)
    with closing(sqlite3.connect(database)) as live:
        live.execute("PRAGMA journal_mode=WAL")
        live.execute("INSERT INTO messages VALUES ('committed in WAL')")
        live.commit()
        assert Path(str(database) + "-wal").stat().st_size > 0

        result, events = _run_backup(
            tmp_path, active=active, process_active=process_active, platform=platform
        )

        assert result.returncode == 0, result.stderr
        assert "service remains active" in result.stderr
        assert any(
            "is-active" in event if platform == "linux" else "print" in event
            for event in events
        )
        backups = list(database.parent.glob("webui.db.bak-*"))
        assert len(backups) == 1
        assert backups[0].stat().st_mode & 0o777 == 0o600
        with closing(sqlite3.connect(backups[0])) as backup:
            assert backup.execute("PRAGMA integrity_check").fetchone() == ("ok",)
            assert backup.execute(
                "SELECT text FROM messages ORDER BY rowid"
            ).fetchall() == [
                ("before upgrade",),
                ("committed in WAL",),
            ]


@pytest.mark.parametrize("suffix", ["-wal", "-shm"])
def test_companion_copy_failure_warns_and_continues(tmp_path, suffix):
    database = _database(tmp_path)
    Path(str(database) + suffix).write_bytes(b"companion fixture")

    result, _ = _run_backup(tmp_path, failed_copy=suffix)

    assert result.returncode == 0, result.stderr
    assert (
        f"Failed to back up webui.db{suffix}; backup may be incomplete" in result.stderr
    )
    assert "upgrade-reached" in result.stdout


@pytest.mark.parametrize("active", [False, True])
def test_main_backup_failure_aborts_upgrade(tmp_path, active):
    database = _database(tmp_path)
    if active:
        database.write_bytes(b"invalid SQLite database")

    result, _ = _run_backup(
        tmp_path, active=active, failed_copy="" if active else "webui.db"
    )

    assert result.returncode != 0
    assert "Failed to back up webui.db before upgrading to 0.12.0" in result.stderr
    assert "upgrade-reached" not in result.stdout
