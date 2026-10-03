import logging
import os
import re
import sqlite3
from contextlib import closing
from datetime import date
from importlib import resources
from pathlib import Path

from my_team.db.engine import latest_version
from my_team.errors import Invalid
from my_team.paths import ensure_private_dir, private_data_dir, projects_dir

KEEP = 7
log = logging.getLogger("my_team.backup")
_CREATE = re.compile(r"CREATE\s+(?:TEMP\s+)?(TRIGGER|VIEW)\s+(?:IF\s+NOT\s+EXISTS\s+)?(\w+)", re.IGNORECASE)


def databases() -> list[tuple[Path, str]]:
    """Every database with its migration package."""
    registry = private_data_dir() / "registry.db"
    found = [(registry, "registry")] if registry.is_file() else []
    return found + [(path, "project") for path in sorted(projects_dir().glob("*.db"))]


def backups_for(stem: str) -> list[dict]:
    """Newest-first snapshots for one database, from the backups folder only."""
    folder = private_data_dir() / "backups"
    out = []
    for path in sorted(folder.glob(f"{stem}-????-??-??.db"), reverse=True):
        try:
            stat = path.stat()
        except OSError:
            continue
        out.append({"name": path.name, "size": stat.st_size, "mtime_ms": int(stat.st_mtime * 1000)})
    return out


def snapshot_before_restore(path: Path, now_ms: int) -> Path | None:
    """Consistent spare copy under a name the listing glob never matches. None when nothing to spare."""
    if not path.is_file():
        return None
    folder = ensure_private_dir(private_data_dir() / "backups")
    target = folder / f"{path.stem}-{date.today().isoformat()}-pre-restore-{now_ms}.db"
    with closing(sqlite3.connect(path)) as conn:
        conn.execute("PRAGMA busy_timeout=5000")
        conn.execute("VACUUM INTO ?", (str(target),))
    return target


def _expected_programmables() -> set[tuple[str, str]]:
    """Trigger/view names our own migrations create; anything else in a backup is foreign."""
    names = set()
    for package in ("project", "registry"):
        try:
            root = resources.files(f"my_team.db.migrations.{package}")
        except (ImportError, FileNotFoundError):
            continue
        for child in sorted(root.iterdir()):
            if child.suffix == ".sql":
                names.update((kind.lower(), name) for kind, name in _CREATE.findall(child.read_text()))
    return names


def verify_backup(path: Path, package: str) -> None:
    """Integrity, exact schema version, and no foreign triggers or views. Raises Invalid."""
    try:
        with closing(sqlite3.connect(f"file:{path}?mode=ro", uri=True)) as conn:
            if conn.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise Invalid("corrupt_backup", f"{path.name} fails integrity_check.")
            version = conn.execute("PRAGMA user_version").fetchone()[0]
            if version != latest_version(package):
                raise Invalid("schema_mismatch", f"{path.name} is schema {version}, this my-team expects "
                                                 f"{latest_version(package)}.")
            objects = {(row[0], row[1]) for row in
                       conn.execute("SELECT type, name FROM sqlite_master WHERE type IN ('trigger', 'view')")}
    except sqlite3.Error as exc:
        raise Invalid("unreadable_backup", f"{path.name} cannot be read: {exc}.") from exc
    if foreign := objects - _expected_programmables():
        raise Invalid("unexpected_objects", f"{path.name} has foreign triggers/views: {sorted(foreign)}.")


def backup_all(force: bool = False) -> list[Path]:
    """One VACUUM INTO snapshot per database per day, or before a pending migration; keeps the newest KEEP."""
    folder = ensure_private_dir(private_data_dir() / "backups")
    written = []
    for path, package in databases():
        target = folder / f"{path.stem}-{date.today().isoformat()}.db"
        tmp = target.with_name(f".{target.name}.tmp")
        try:
            # Plain connection: engine.connect would switch journal modes, and read-only fails on a crashed WAL.
            with closing(sqlite3.connect(path)) as conn:
                pending = conn.execute("PRAGMA user_version").fetchone()[0] < latest_version(package)
                if target.exists() and not (force or pending):
                    continue
                tmp.unlink(missing_ok=True)
                conn.execute("VACUUM INTO ?", (str(tmp),))
            os.replace(tmp, target)
            written.append(target)
        except (OSError, sqlite3.Error):
            log.exception("backup of %s failed", path.name)
        for old in sorted(folder.glob(f"{path.stem}-????-??-??.db"))[:-KEEP]:
            try:
                old.unlink()
            except OSError:
                pass  # locked on Windows; the next start retries
    return written
