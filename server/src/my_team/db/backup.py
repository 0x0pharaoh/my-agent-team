import logging
import os
import sqlite3
from contextlib import closing
from datetime import date
from pathlib import Path

from my_team.db.engine import latest_version
from my_team.paths import ensure_private_dir, private_data_dir, projects_dir

KEEP = 7
log = logging.getLogger("my_team.backup")


def databases() -> list[tuple[Path, str]]:
    """Every database with its migration package."""
    registry = private_data_dir() / "registry.db"
    found = [(registry, "registry")] if registry.is_file() else []
    return found + [(path, "project") for path in sorted(projects_dir().glob("*.db"))]


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
