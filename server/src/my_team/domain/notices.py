from my_team.db.engine import Tx
from my_team.domain import messages, questions, tickets

ANSWER_REPLAY_MS = 24 * 3600_000


def collect(tx: Tx, session, include_pending: bool = True, now: int | None = None) -> dict:
    cursor = tx.scalar("SELECT notice_cursor FROM sessions WHERE id = ?", (session["id"],))
    stop, assigned = tickets.pending_notices(tx, session, cursor, include_pending)
    new_messages = tx.scalar(
        "SELECT COUNT(*) FROM events e JOIN message_recipients r ON r.message_id = e.entity_id"
        " WHERE e.id > ? AND e.type = 'message.sent' AND r.recipient_type = 'agent' AND r.recipient_id = ?"
        " AND r.read_ms IS NULL", (cursor, session["agent_id"]))
    result = {"stop": stop, "assigned": assigned, "answers": questions.answered_since(tx, session["agent_id"], cursor,
                                                  now - ANSWER_REPLAY_MS if include_pending and now else None),
              "new_messages": new_messages, "unread": messages.unread_count(tx, "agent", session["agent_id"])}
    tx.execute("UPDATE sessions SET notice_cursor = (SELECT COALESCE(MAX(id), 0) FROM events) WHERE id = ?",
               (session["id"],))
    return result


def has_news(notice: dict) -> bool:
    return bool(notice["stop"] or notice["assigned"] or notice["answers"] or notice["new_messages"])
