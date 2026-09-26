import json
import os
import re
import sqlite3
import subprocess
import tempfile
import tomllib
from contextlib import closing
from pathlib import Path

from my_team import __version__, activation, client, git
from my_team.db import backup
from my_team.db.engine import latest_version
from my_team.domain.repo import git_too_old
from my_team.installers.codex import codex_home
from my_team.installers.hermes import config_path as hermes_config_path
from my_team.installers.hermes import hermes_home
from my_team.installers.opencode import config_dir as opencode_config_dir
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
    yield from _adapters()


def _adapters():
    home = Path(os.environ.get("HOME") or Path.home())
    yield _claude_plugin(home)
    yield _agent_skill(home)
    yield _codex()
    yield _opencode(home)
    yield _hermes()


def _claude_plugin(home: Path) -> tuple[str, str]:
    try:
        settings = json.loads((home / ".claude" / "settings.json").read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError, OSError):
        return "warn", "Claude Code settings not found; run `my-team install claude-code`"
    if settings.get("enabledPlugins", {}).get("my-team@my-team") is True:
        return "pass", "Claude Code plugin my-team enabled"
    return "warn", "Claude Code plugin my-team not enabled"


def _agent_skill(home: Path) -> tuple[str, str]:
    if (home / ".agents" / "skills" / "my-team" / "SKILL.md").is_file():
        return "pass", "skill ~/.agents/skills/my-team/SKILL.md present"
    return "warn", "skill missing under ~/.agents/skills/my-team"


def _codex() -> tuple[str, str]:
    try:
        data = tomllib.loads((codex_home() / "config.toml").read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError, OSError):
        return "warn", "Codex config not found; run `my-team install codex`"
    if data.get("mcp_servers", {}).get("my-team"):
        return "pass", "Codex MCP server my-team registered"
    return "warn", "Codex config lacks mcp_servers.my-team"


def _opencode(home: Path) -> tuple[str, str]:
    target = opencode_config_dir(home)
    if not (target / "plugins" / "my-team.js").is_file():
        return "warn", "OpenCode plugin missing; run `my-team install opencode`"
    for name in ("opencode.jsonc", "opencode.json"):
        if (target / name).is_file():
            return (("pass", "OpenCode plugin + MCP entry present") if _mcp_entry(target / name)
                    else ("warn", f"OpenCode plugin present but no MCP entry in {name}"))
    return "warn", "OpenCode plugin present but no opencode.json(c) found"


def _mcp_entry(path: Path) -> bool:
    try:
        data = json.loads(_without_comments(path.read_text(encoding="utf-8")))
    except (ValueError, OSError):
        return False
    return bool(data.get("mcp", {}).get("servers", {}).get("my-team"))


def _without_comments(text: str) -> str:
    """Strip // and /* */ comments plus trailing commas outside strings, for JSONC sniffing."""
    out, i, string = [], 0, None
    while i < len(text):
        ch = text[i]
        if string:
            out.append(ch)
            if ch == "\\":
                out.append(text[i + 1:i + 2])
                i += 2
                continue
            if ch == string:
                string = None
        elif ch in "\"'":
            string = ch
            out.append(ch)
        elif text.startswith("//", i):
            while i < len(text) and text[i] != "\n":
                i += 1
            continue
        elif text.startswith("/*", i):
            end = text.find("*/", i + 2)
            i = len(text) if end < 0 else end + 2
            continue
        else:
            out.append(ch)
        i += 1
    return re.sub(r",(\s*[}\]])", r"\1", "".join(out))


def _hermes() -> tuple[str, str]:
    try:
        text = hermes_config_path(hermes_home()).read_text(encoding="utf-8")
    except OSError:
        return "warn", "Hermes config not found; run `my-team install hermes`"
    if "--agent hermes" in text:
        return "pass", "Hermes hooks reference my-team"
    return "warn", "Hermes config lacks my-team hooks"


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
