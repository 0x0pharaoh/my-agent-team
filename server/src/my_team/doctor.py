import json
import os
import re
import sqlite3
import subprocess
import tempfile
from contextlib import closing
from pathlib import Path

from my_team import __version__, activation, client, git
from my_team.db import backup
from my_team.db.engine import latest_version
from my_team.domain.repo import git_too_old
from my_team.paths import _current_user_sid, data_dir

# Administrators and Owner Rights come from Python's mkdir(mode=0o700) on Windows; admins can take ownership anyway.
# LA is the built-in Administrator account (RID 500): admin-equivalent, so allowing BA already covers it.
ALLOWED_SIDS = {"SY", "BA", "OW", "LA"}


def run() -> int:
    failed = False
    for level, message in _checks():
        print(f"{level:<5} {message}")
        failed |= level == "fail"
    return int(failed)


def _checks():
    data = data_dir()
    if not data.is_dir():
        yield "fail", f"data dir {data} is missing: run `my-team install <agent>` or `my-team serve`"
        return
    yield _private(data)
    secret = data / "secret"
    if not (secret.is_file() and secret.stat().st_size == 32):
        yield "fail", f"secret {secret} is missing or malformed"
    else:
        yield "pass", "secret present"
        yield _daemon(secret.read_bytes())
    for path, package in backup.databases():
        yield _database(path, package)
    yield _activation()
    yield _git()


def _private(path: Path) -> tuple[str, str]:
    if os.name != "nt":
        mode = path.stat().st_mode & 0o777
        return ("fail", f"{path} is open to other users (mode {oct(mode)})") if mode & 0o077 else ("pass", f"{path} is private")
    try:
        with tempfile.TemporaryDirectory() as tmp:
            subprocess.run(["icacls", str(path), "/save", f"{tmp}/acl"], capture_output=True, check=True)
            sddl = Path(tmp, "acl").read_text(encoding="utf-16-le").splitlines()[1]
        others = set(re.findall(r"\(A;[^;]*;[^;]*;;;([^)]+)\)", sddl)) - ALLOWED_SIDS - {_current_user_sid()}
    except (OSError, ValueError, subprocess.CalledProcessError, IndexError) as exc:
        return "fail", f"could not read the ACL of {path}: {exc}"
    if others:
        return "fail", f"{path} also grants access to {', '.join(sorted(others))}; delete it or reset its ACL"
    return "pass", f"{path} is private"


def _daemon(secret: bytes) -> tuple[str, str]:
    info = client._server_info()
    if info is None:
        return "warn", "daemon not running (it starts on demand)"
    if not client._verified(info, secret):
        return "warn", f"server.json names port {info.get('port')} but nothing there proves it is this daemon"
    if info.get("version") != __version__:
        return "warn", f"daemon {info.get('version')} differs from CLI {__version__}; restart the daemon"
    return "pass", f"daemon {__version__} running on port {info['port']}"


def _database(path: Path, package: str) -> tuple[str, str]:
    try:
        with closing(sqlite3.connect(path)) as conn:
            integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
            version = conn.execute("PRAGMA user_version").fetchone()[0]
    except sqlite3.Error as exc:
        return "fail", f"{path.name}: {exc}"
    latest = latest_version(package)
    if integrity != "ok":
        return "fail", f"{path.name}: integrity check says {integrity}; restore from backups/"
    if version > latest:
        return "fail", f"{path.name}: schema {version} is newer than this my-team ({latest}); upgrade my-team"
    if version < latest:
        return "warn", f"{path.name}: schema {version}/{latest}, migrates when the daemon next opens it"
    return "pass", f"{path.name}: integrity ok, schema {version}"


def _activation() -> tuple[str, str]:
    try:
        json.loads(activation._path().read_text(encoding="utf-8"))
    except FileNotFoundError:
        return "pass", "activation: nothing recorded yet (off everywhere)"
    except (OSError, ValueError) as exc:
        return "fail", f"activation file {activation._path()} is unreadable: {exc}"
    return "pass", "activation file ok"


def _git() -> tuple[str, str]:
    try:
        old = git_too_old(Path.cwd())
    except git.GitError as exc:
        return "warn", f"git not usable: {exc}"
    return ("warn", old) if old else ("pass", "git meets the security floor")
