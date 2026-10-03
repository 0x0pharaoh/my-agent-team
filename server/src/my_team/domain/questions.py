import json

from my_team.actor import Actor
from my_team.db.engine import Tx
from my_team.domain import events
from my_team.errors import Conflict, NotFound
from my_team.ids import new_id
from my_team.redact import redact

_SELECT = ("SELECT q.*, t.key AS ticket_key, a.display_name AS asked_by FROM questions q"
           " LEFT JOIN tickets t ON t.id = q.ticket_id LEFT JOIN agents a ON a.id = q.asked_by_agent")


def _serialize(row) -> dict:
    return {"id": row["id"], "kind": row["kind"], "prompt": row["prompt"], "options": json.loads(row["options"]),
            "recommendation": row["recommendation"], "answer": row["answer"], "status": row["status"],
            "ticket": row["ticket_key"], "asked_by": row["asked_by"], "created_ms": row["created_ms"],
            "answered_ms": row["answered_ms"]}


def ask(tx: Tx, actor: Actor, now: int, *, kind: str, prompt: str, options: list[str], recommendation: str | None,
        ticket: str | None) -> dict:
    prompt, recommendation = redact(prompt), redact(recommendation)
    ticket_id = tx.scalar("SELECT id FROM tickets WHERE key = ?", (ticket,)) if ticket else None
    if ticket and ticket_id is None:
        raise NotFound("unknown_ticket", f"No ticket {ticket}.")
    question_id = new_id()
    tx.execute("INSERT INTO questions (id, kind, prompt, options, recommendation, ticket_id, asked_by_agent,"
               " asked_by_session, created_ms) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
               (question_id, kind, prompt, json.dumps([redact(o) for o in options]), recommendation,
                ticket_id, actor.agent_id, actor.session_id, now))
    events.emit(tx, "question.asked", "question", question_id, actor, {"kind": kind, "prompt": prompt}, now)
    return get(tx, question_id)


def get(tx: Tx, question_id: str) -> dict:
    row = tx.one(f"{_SELECT} WHERE q.id = ?", (question_id,))
    if row is None:
        raise NotFound("unknown_question", "No such question.")
    return _serialize(row)


def answer(tx: Tx, actor: Actor, now: int, question_id: str, text: str, reject: bool) -> dict:
    current = get(tx, question_id)
    if current["status"] != "open":
        raise Conflict("already_answered", "This question was already answered.")
    row = tx.one("SELECT asked_by_agent FROM questions WHERE id = ?", (question_id,))
    tx.execute("UPDATE questions SET answer = ?, status = ?, answered_ms = ? WHERE id = ?",
               (redact(text), "rejected" if reject else "answered", now, question_id))
    events.emit(tx, "question.answered", "question", question_id, actor,
                {"asked_by_agent": row["asked_by_agent"], "status": "rejected" if reject else "answered"}, now)
    return get(tx, question_id)


def listing(tx: Tx, status: str | None = None, limit: int = 200) -> list[dict]:
    if status:
        rows = tx.all(f"{_SELECT} WHERE q.status = ? ORDER BY q.created_ms DESC LIMIT ?", (status, limit))
    else:
        rows = tx.all(f"{_SELECT} ORDER BY q.created_ms DESC LIMIT ?", (limit,))
    return [_serialize(row) for row in rows]


def answered_since(tx: Tx, agent_id: str, cursor: int, since_ms: int | None = None) -> list[dict]:
    """Answers after the cursor; with since_ms, every answer after that time (a fresh session on the seat)."""
    ids = [r["entity_id"] for r in tx.all(
        "SELECT entity_id FROM events WHERE (id > ? OR created_ms > ?) AND type = 'question.answered'"
        " AND json_extract(payload, '$.asked_by_agent') = ?", (cursor, since_ms or 2**62, agent_id))]
    return [get(tx, question_id) for question_id in ids]
