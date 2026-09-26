import functools
import hashlib
import json
import re
from pathlib import Path, PurePosixPath, PureWindowsPath

from my_team.actor import Actor
from my_team.db.engine import Tx
from my_team.domain import events
from my_team.errors import Conflict, Forbidden, Invalid, NotFound, RateLimited
from my_team.ids import new_id
from my_team.redact import redact
from my_team.scan import _blob_id

AGENT_KINDS = ("fact", "work_summary", "note")
WRITES_PER_HOUR = 60
NOTE_TTL_MS = 7 * 24 * 3600_000
MAX_FILE_BYTES = 1024 * 1024

_SELECT = ("SELECT m.rowid AS rid, m.*, t.key AS ticket_key, a.display_name AS author_name FROM memories m"
           " LEFT JOIN tickets t ON t.id = m.ticket_id LEFT JOIN agents a ON a.id = m.author_id")


def _clean(rel: str) -> str:
    """Rejects absolute, drive, UNC and parent paths by text, before anything touches the filesystem."""
    rel = rel.replace("\\", "/").strip()
    if not rel or PureWindowsPath(rel).anchor or PurePosixPath(rel).is_absolute() or ".." in PurePosixPath(rel).parts:
        raise Invalid("bad_path", f"{rel!r} must be a path inside the project.")
    return rel


@functools.lru_cache(maxsize=4096)  # ponytail: in-process cache keyed on mtime+size; a same-tick rewrite can be missed
def _hash(path: str, mtime_ns: int, size: int) -> str:
    return _blob_id(Path(path).read_bytes())


def _blob(root: Path, rel: str) -> str | None:
    full = (root / _clean(rel)).resolve()
    if not full.is_relative_to(root.resolve()):
        raise Invalid("bad_path", f"{rel} is outside the project.")
    if not full.is_file():
        return None
    stat = full.stat()
    return None if stat.st_size > MAX_FILE_BYTES else _hash(str(full), stat.st_mtime_ns, stat.st_size)


def _current(root: Path, rel: str) -> str | None:
    try:
        return _blob(root, rel)
    except (Invalid, OSError):
        return "unreadable"


def _stale(root: Path, files: list[dict]) -> list[str]:
    return [f["path"] for f in files if _current(root, f["path"]) != f["blob"]]


def serialize(row, root: Path) -> dict:
    files = json.loads(row["files"])
    return {"id": row["id"], "kind": row["kind"], "title": row["title"], "body": row["body"], "files": files,
            "tags": row["tags"], "ticket": row["ticket_key"], "source": row["source"], "status": row["status"],
            "author": row["author_name"] or row["author_type"], "created_ms": row["created_ms"],
            "supersedes_id": row["supersedes_id"], "stale": _stale(root, files)}


def _ticket_id(tx: Tx, key: str | None) -> str | None:
    if not key:
        return None
    ticket_id = tx.scalar("SELECT id FROM tickets WHERE key = ?", (key,))
    if ticket_id is None:
        raise NotFound("unknown_ticket", f"No ticket {key}.")
    return ticket_id


def search(tx: Tx, root: Path, now: int, *, q: str | None = None, ids: list[str] | None = None,
           ticket: str | None = None, kinds: list[str] | None = None, limit: int = 8,
           statuses: tuple[str, ...] = ("active", "needs_review")) -> list[dict]:
    clauses = [f"m.status IN ({','.join('?' * len(statuses))})", "(m.expires_ms IS NULL OR m.expires_ms > ?)"]
    params: list = [*statuses, now]
    if kinds:
        clauses.append(f"m.kind IN ({','.join('?' * len(kinds))})")
        params.extend(kinds)
    if ids:
        clauses.append(f"m.id IN ({','.join('?' * len(ids))})")
        params.extend(ids)
    where = " AND ".join(clauses)
    words = re.findall(r"\w+", q or "")[:20]
    if words:
        match = " OR ".join(f'"{word}"' for word in words)
        rows = tx.all(f"SELECT m.* FROM ({_SELECT}) m JOIN memories_fts ON memories_fts.rowid = m.rid"
                      f" WHERE memories_fts MATCH ? AND {where} ORDER BY bm25(memories_fts) LIMIT 50",
                      (match, *params))
    else:
        rows = tx.all(f"SELECT * FROM ({_SELECT}) m WHERE {where} ORDER BY m.created_ms DESC LIMIT 50", params)
    results = []
    for rank, row in enumerate(rows):
        item = serialize(row, root)
        boost = (2.0 if ticket and item["ticket"] == ticket else 1.0) * \
                (1.3 if item["source"] == "human_confirmed" else 1.0) * (0.5 if item["stale"] else 1.0)
        recency = 1 / (1 + (now - item["created_ms"]) / (30 * 24 * 3600_000))
        item["score"] = round(boost * recency / (1 + rank), 4)
        results.append(item)
    return sorted(results, key=lambda item: item["score"], reverse=True)[:limit]


def get(tx: Tx, root: Path, memory_id: str) -> dict:
    row = tx.one(f"{_SELECT} WHERE m.id = ?", (memory_id,))
    if row is None:
        raise NotFound("unknown_memory", f"No memory {memory_id}.")
    return serialize(row, root)


def _check_quota(tx: Tx, actor: Actor, now: int) -> None:
    recent = tx.scalar(
        "SELECT COUNT(*) FROM memories m JOIN agents a ON a.id = m.author_id WHERE m.created_ms > ?"
        " AND a.agent_type = (SELECT agent_type FROM agents WHERE id = ?)", (now - 3600_000, actor.agent_id))
    if recent >= WRITES_PER_HOUR:
        raise RateLimited("quota_exceeded", "Memory write quota reached for this agent type; try again later.")


def write(tx: Tx, actor: Actor, root: Path, now: int, *, kind: str, title: str, body: str = "",
          files: list[str] | None = None, tags: str = "", ticket: str | None = None,
          supersedes_id: str | None = None) -> dict:
    if actor.is_agent and kind not in AGENT_KINDS:
        raise Forbidden("human_only", "Only the human records human instructions.")
    title, body, tags = redact(title.strip()), redact(body.strip()), redact(tags.strip())
    if not title:
        raise Invalid("title_required", "A memory needs a title.")
    paths = list(dict.fromkeys(_clean(p) for p in (files or [])[:50]))
    digest = hashlib.sha256("\0".join((kind, title, body)).encode()).hexdigest()
    duplicate = tx.scalar("SELECT id FROM memories WHERE content_hash = ? AND status = 'active' AND id IS NOT ?"
                          " AND (expires_ms IS NULL OR expires_ms > ?)", (digest, supersedes_id, now))
    if duplicate:
        return {"memory": get(tx, root, duplicate), "duplicate": True, "similar": []}
    if actor.is_agent:
        _check_quota(tx, actor, now)
    similar = [{"id": m["id"], "title": m["title"]}
               for m in search(tx, root, now, q=f"{title} {body}", kinds=[kind], limit=3)]
    memory_id = new_id()
    tx.execute(
        "INSERT INTO memories (id, kind, title, body, files, tags, ticket_id, source, supersedes_id, content_hash,"
        " author_type, author_id, session_id, expires_ms, created_ms) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (memory_id, kind, title, body, json.dumps([{"path": p, "blob": _blob(root, p)} for p in paths]),
         tags, _ticket_id(tx, ticket), "human_confirmed" if actor.is_human else "agent_reported", supersedes_id,
         digest, actor.kind, actor.agent_id or actor.id, actor.session_id,
         now + NOTE_TTL_MS if kind == "note" else None, now),
    )
    events.emit(tx, "memory.written", "memory", memory_id, actor, {"kind": kind, "title": title}, now)
    return {"memory": get(tx, root, memory_id), "duplicate": False, "similar": similar}


def correct(tx: Tx, actor: Actor, root: Path, now: int, memory_id: str, action: str,
            title: str | None = None, body: str | None = None) -> dict:
    current = get(tx, root, memory_id)
    row = tx.one("SELECT author_id, kind, source FROM memories WHERE id = ?", (memory_id,))
    if action == "supersede":
        if not title:
            raise Invalid("title_required", "A superseding memory needs a title.")
        if actor.is_agent and (row["kind"] not in AGENT_KINDS or row["source"] == "human_confirmed"):
            raise Forbidden("human_only", "Only the human replaces human instructions or confirmed memories; "
                            "flag it for review instead.")
        result = write(tx, actor, root, now, kind=row["kind"], title=title, body=body or "",
                       files=[f["path"] for f in current["files"]], ticket=current["ticket"], supersedes_id=memory_id)
        tx.execute("UPDATE memories SET status = 'superseded' WHERE id = ?", (memory_id,))
    elif action in ("retract", "flag", "confirm"):
        if action == "retract" and actor.is_agent and row["author_id"] != actor.agent_id:
            raise Forbidden("not_author", "Agents may retract only their own memories; flag it instead.")
        if action == "confirm" and not actor.is_human:
            raise Forbidden("human_only", "Only the human confirms memories.")
        change = {"retract": "status = 'retracted'", "flag": "status = 'needs_review'",
                  "confirm": "status = 'active', source = 'human_confirmed'"}[action]
        allowed = {"retract": ("active", "needs_review"), "flag": ("active",),
                   "confirm": ("active", "needs_review", "retracted", "superseded")}[action]
        if tx.execute(f"UPDATE memories SET {change} WHERE id = ? AND status IN ({','.join('?' * len(allowed))})",
                      (memory_id, *allowed)).rowcount == 0:
            raise Conflict("invalid_state", f"A {current['status']} memory cannot be {action}ed.")
        result = {"memory": get(tx, root, memory_id)}
    else:
        raise Invalid("unknown_action", "Action must be supersede, retract, flag or confirm.")
    events.emit(tx, f"memory.{action}", "memory", memory_id, actor, {"title": current["title"]}, now)
    return result
