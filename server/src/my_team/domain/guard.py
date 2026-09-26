import fnmatch
import json
import os
from pathlib import Path

from my_team.db.engine import Tx

FREE_PREFIXES = ("docs/", ".my-team/")


def relative(root: Path, path: str) -> str | None:
    """Project-relative POSIX path, or None when the file is outside the project."""
    full = Path(path) if os.path.isabs(path) else root / path
    try:
        return full.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return None


def matches(rel: str, pattern: str) -> bool:
    """Globs use fnmatch, so * also crosses directories; a plain path matches itself and everything below it."""
    pattern = pattern.replace("\\", "/").strip().lstrip("./")
    if any(ch in pattern for ch in "*?["):
        return fnmatch.fnmatchcase(rel, pattern)
    return rel == pattern or rel.startswith(pattern.rstrip("/") + "/")


def check(tx: Tx, session, root: Path, path: str) -> dict:
    rel = relative(root, path)
    if rel is None or rel.startswith(FREE_PREFIXES):
        return {"decision": "allow", "path": rel}
    mine = tx.scalar("SELECT key FROM tickets WHERE active_session_id = ? AND status = 'in_progress'", (session["id"],))
    if mine is None:
        return {"decision": "ask", "path": rel,
                "reason": f"my-team: no claimed ticket for this edit to {rel}. Claim one with ticket_claim (or propose "
                          "one with ticket_create) before implementation edits."}
    others = tx.all("SELECT t.key, t.paths, a.display_name AS holder FROM tickets t JOIN sessions s ON s.id = "
                    "t.active_session_id JOIN agents a ON a.id = s.agent_id WHERE t.status = 'in_progress' AND "
                    "t.active_session_id <> ?", (session["id"],))
    for other in others:
        if any(matches(rel, pattern) for pattern in json.loads(other["paths"])):
            return {"decision": "ask", "path": rel,
                    "reason": f"my-team: {rel} is claimed by {other['holder']} for {other['key']}. Coordinate with "
                              f"message_send before editing it."}
    return {"decision": "allow", "path": rel, "ticket": mine}
