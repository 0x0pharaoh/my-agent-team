import json

from my_team.actor import Actor
from my_team.db.engine import Tx
from my_team.domain import events
from my_team.errors import Conflict, NotFound
from my_team.ids import new_id
from my_team.redact import redact

_SELECT = ("SELECT q.*, t.key AS ticket_key, a.display_name AS asked_by, lead.display_name AS routed_to"
           " FROM questions q LEFT JOIN tickets t ON t.id = q.ticket_id LEFT JOIN agents a ON a.id = q.asked_by_agent"
           " LEFT JOIN agents lead ON lead.id = q.routed_to_agent_id")


def _serialize(row) -> dict:
    return {"id": row["id"], "kind": row["kind"], "prompt": row["prompt"], "options": json.loads(row["options"]),
            "recommendation": row["recommendation"], "answer": row["answer"], "status": row["status"],
            "ticket": row["ticket_key"], "asked_by": row["asked_by"], "created_ms": row["created_ms"],
            "answered_ms": row["answered_ms"], "routed_to": row["routed_to"], "escalated_ms": row["escalated_ms"]}


def ask(tx: Tx, actor: Actor, now: int, *, kind: str, prompt: str, options: list[str], recommendation: str | None,
        ticket: str | None, route: bool = False) -> dict:
    """With route, a plain question goes to the asker's lead first; decisions always go to the human."""
    prompt, recommendation = redact(prompt), redact(recommendation)
    lead = tx.scalar("SELECT lead_id FROM agents WHERE id = ?", (actor.agent_id,)) if (
        route and kind == "question" and actor.agent_id) else None
    ticket_id = tx.scalar("SELECT id FROM tickets WHERE key = ?", (ticket,)) if ticket else None
    if ticket and ticket_id is None:
        raise NotFound("unknown_ticket", f"No ticket {ticket}.")
    question_id = new_id()
    tx.execute("INSERT INTO questions (id, kind, prompt, options, recommendation, ticket_id, asked_by_agent,"
               " asked_by_session, routed_to_agent_id, created_ms) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
               (question_id, kind, prompt, json.dumps([redact(o) for o in options]), recommendation,
                ticket_id, actor.agent_id, actor.session_id, lead, now))
    events.emit(tx, "question.asked", "question", question_id, actor,
                {"kind": kind, "prompt": prompt, "routed_to": lead}, now)
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


def _routed(tx: Tx, actor: Actor, question_id: str) -> dict:
    current = get(tx, question_id)
    routed = tx.scalar("SELECT routed_to_agent_id FROM questions WHERE id = ?", (question_id,))
    if current["status"] != "open" or routed is None or routed != actor.agent_id:
        raise Conflict("not_routed_to_you", "Only an open question routed to your seat can be answered or escalated.")
    return current


def reply(tx: Tx, actor: Actor, now: int, question_id: str, text: str) -> dict:
    """A lead answers a question its worker routed to it."""
    _routed(tx, actor, question_id)
    return answer(tx, actor, now, question_id, text, reject=False)


def escalate(tx: Tx, actor: Actor, now: int, question_id: str, force: bool = False) -> dict:
    """Pass a routed question up to the human (force: the system, when a lead's run ended without answering)."""
    if not force:
        _routed(tx, actor, question_id)
    tx.execute("UPDATE questions SET routed_to_agent_id = NULL, escalated_ms = ? WHERE id = ? AND status = 'open'",
               (now, question_id))
    events.emit(tx, "question.escalated", "question", question_id, actor, {}, now)
    return get(tx, question_id)


def routed_to(tx: Tx, agent_id: str, limit: int = 5) -> list[dict]:
    rows = tx.all(f"{_SELECT} WHERE q.status = 'open' AND q.routed_to_agent_id = ? ORDER BY q.created_ms LIMIT ?",
                  (agent_id, limit))
    return [_serialize(row) for row in rows]


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
