from pathlib import Path

from my_team import activation
from my_team.db.engine import Tx
from my_team.domain import docs, projects
from my_team.errors import Invalid

STALE_MS = 3600_000


def claim(tx: Tx, session_id: str | None, repo_key: str, now: int) -> dict:
    """One holder per repo; the holder re-enters, others take over only stale claims."""
    if not session_id:
        raise Invalid("no_session", "Init claims need an agent session.")
    row = tx.one("SELECT * FROM init_claims WHERE repo_key = ?", (repo_key,))
    if row is None or row["holder_session_id"] == session_id:
        epoch = row["epoch"] if row else 1
        tx.execute("INSERT INTO init_claims (repo_key, holder_session_id, epoch, claimed_ms) VALUES (?, ?, ?, ?)"
                   " ON CONFLICT(repo_key) DO UPDATE SET holder_session_id = excluded.holder_session_id,"
                   " epoch = excluded.epoch, claimed_ms = excluded.claimed_ms",
                   (repo_key, session_id, epoch, now))
        return {"claimed": True, "holder_session_id": session_id, "epoch": epoch, "claimed_ms": now}
    if now - row["claimed_ms"] > STALE_MS:
        epoch = row["epoch"] + 1
        tx.execute("UPDATE init_claims SET holder_session_id = ?, epoch = ?, claimed_ms = ? WHERE repo_key = ?",
                   (session_id, epoch, now, repo_key))
        return {"claimed": True, "holder_session_id": session_id, "epoch": epoch, "claimed_ms": now}
    return {"claimed": False, "holder_session_id": row["holder_session_id"], "epoch": row["epoch"],
            "claimed_ms": row["claimed_ms"]}


def release(tx: Tx, repo_key: str) -> dict:
    cursor = tx.execute("DELETE FROM init_claims WHERE repo_key = ?", (repo_key,))
    return {"released": cursor.rowcount > 0}


def checklist(tx: Tx, root: Path) -> dict:
    """Progress derived from existing rows: nothing about init is stored."""
    toml = projects.read_project_file(root) is not None
    doc_rows = {}
    for name in docs.NAMES:
        summary = docs.summary(root, name)
        pending = tx.scalar("SELECT COUNT(*) FROM doc_proposals WHERE doc = ? AND status = 'pending'",
                            (name,)) or 0
        doc_rows[name] = {"present": summary["present"], "sections": len(summary.get("sections", [])),
                          "pending_proposals": pending}
    open_questions = tx.scalar("SELECT COUNT(*) FROM questions WHERE status = 'open'") or 0
    init_tickets = tx.scalar("SELECT COUNT(*) FROM tickets WHERE origin_key LIKE 'init:%'"
                             " AND status NOT IN ('done', 'cancelled')") or 0
    active = activation.resolve(activation.load(), "", None, root)["active"]
    complete = toml and all(d["present"] for d in doc_rows.values()) and active
    return {"checklist": {"project_toml": toml, "docs": doc_rows, "open_questions": open_questions,
                          "init_tickets": init_tickets, "activation": active, "scan": None},
            "complete": complete}
