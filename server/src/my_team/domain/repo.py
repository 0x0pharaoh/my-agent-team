import os
import re
from pathlib import Path

from my_team import git
from my_team.errors import Forbidden, Invalid, Unavailable
from my_team.redact import redact

RISKY = re.compile(
    r"core\.(fsmonitor|sshcommand|gitproxy|askpass|alternaterefscommand|pager|editor)|filter\..+\.(clean|smudge|process)"
    r"|diff\..+\.(command|textconv)|merge\..+\.driver|credential\..*|remote\..+\.(uploadpack|receivepack|vcs)"
    r"|protocol\..*|url\..+\.insteadof|gpg\..*|include.*|uploadpack\..*|pager\..*|log\.showsignature")
# ponytail: hand-kept floor (CVE-2026-62960, Git for Windows 2.55.0.windows.4); add a POSIX floor once one is verified.
WINDOWS_FLOOR = (2, 55, 0, 4)
FETCH = ("-c", "protocol.allow=never", "-c", "protocol.https.allow=always", "-c", "protocol.ssh.allow=always",
         "fetch", "--prune")


def _try(root: Path, *args: str) -> str | None:
    try:
        return git.run(root, *args).strip()
    except git.GitError:
        return None


def _strip_userinfo(text: str) -> str:
    return re.sub(r"(?<=://)[^/@\s]+@", "", text)


def risky_keys(root: Path) -> list[str]:
    """Repo-controlled config keys that can make git run commands; global and system config are the user's own."""
    parts = git.run(root, "config", "--list", "--show-scope", "-z").split("\0")
    keys = {entry.split("\n", 1)[0].lower() for scope, entry in zip(parts[::2], parts[1::2])
            if scope in ("local", "worktree")}
    return sorted(key for key in keys if RISKY.fullmatch(key))


def _changes(root: Path) -> dict:
    counts = {"staged": 0, "modified": 0, "untracked": 0}
    entries = iter(git.run(root, "status", "--porcelain=v1", "-z").split("\0"))
    for entry in entries:
        if len(entry) < 3:
            continue
        if entry[:2] == "??":
            counts["untracked"] += 1
            continue
        counts["staged"] += entry[0] != " "
        counts["modified"] += entry[1] != " "
        if entry[0] in "RC":
            next(entries, None)
    return counts


def _fetch_disabled(root: Path) -> str | None:
    version = git.run(root, "--version").strip()
    found = tuple(int(n) for n in re.findall(r"\d+", version)[:4])
    if os.name == "nt" and found < WINDOWS_FLOOR:
        return f"{version} is below the security floor 2.55.0.windows.4; upgrade Git for Windows to enable fetch."
    return None


def status(root: Path) -> dict:
    if _try(root, "rev-parse", "--git-dir") is None:
        return {"git": False}
    blocked = risky_keys(root)
    if blocked:
        return {"git": True, "blocked": blocked}
    log = _try(root, "log", "-20", "--format=%h%x00%s%x00%an%x00%ct") or ""
    commits = [{"sha": sha, "subject": subject, "author": author, "time_ms": int(ct) * 1000}
               for sha, subject, author, ct in (line.split("\0") for line in log.splitlines())]
    upstream = _try(root, "rev-parse", "--abbrev-ref", "@{u}")
    ahead = behind = None
    if upstream and (counts := _try(root, "rev-list", "--left-right", "--count", "HEAD...@{u}")):
        ahead, behind = (int(n) for n in counts.split())
    fetch_head = root / (_try(root, "rev-parse", "--git-path", "FETCH_HEAD") or "FETCH_HEAD")
    remotes = {}
    for line in (_try(root, "remote", "-v") or "").splitlines():
        name, url = line.split()[:2]
        remotes[name] = _strip_userinfo(url)
    return {
        "git": True, "blocked": [], "branch": _try(root, "symbolic-ref", "--short", "-q", "HEAD"),
        "changes": _changes(root), "commits": commits, "upstream": upstream, "ahead": ahead, "behind": behind,
        "fetched_ms": int(fetch_head.stat().st_mtime * 1000) if fetch_head.is_file() else None,
        "remotes": [{"name": name, "url": url} for name, url in remotes.items()],
        "fetch_disabled": _fetch_disabled(root),
    }


def fetch(root: Path) -> dict:
    info = status(root)
    if not info["git"]:
        raise Invalid("not_a_repository", "The project root is not a git repository.")
    if info["blocked"]:
        raise Forbidden("git_config_untrusted", f"This repository's git config sets {', '.join(info['blocked'])}.")
    if info["fetch_disabled"]:
        raise Forbidden("git_too_old", info["fetch_disabled"])
    if not info["remotes"]:
        raise Invalid("no_remote", "This repository has no remote to fetch from.")
    try:
        git.run(root, *FETCH, timeout=120)
    except git.GitError as exc:
        raise Unavailable("fetch_failed", redact(_strip_userinfo(str(exc)))[:500]) from None
    return status(root)
