import re

from my_team.actor import Actor
from my_team.db.engine import Tx
from my_team.domain import events
from my_team.errors import Invalid, NotFound, RateLimited
from my_team.ids import new_id
from my_team.redact import redact

BROADCAST_INTERVAL_MS = 10 * 60_000
OPEN_REQUESTS_PER_SENDER = 5
TICKET_KEY = re.compile(r"^[A-Z][A-Z0-9]{1,9}-\d+$")

_SELECT = (
    "SELECT m.*, a.display_name AS sender_name, t.key AS ticket_key FROM messages m"
    " LEFT JOIN agents a ON a.id = m.sender_id LEFT JOIN tickets t ON t.id = m.ticket_id"
)


def _me(actor: Actor) -> tuple[str, str]:
    return ("human", "human") if actor.is_human else ("agent", actor.agent_id)


def _recipients(tx: Tx, actor: Actor, to: str, reply_to: str | None) -> tuple[str, str | None, str, list]:
    """Returns (channel, ticket_id, thread_id, [(recipient_type, recipient_id)])."""
    me = _me(actor)
    if reply_to:
        parent = tx.one("SELECT * FROM messages WHERE id = ?", (reply_to,))
        if parent is None:
            raise NotFound("unknown_message", "No such message to reply to.")
        sender = (parent["sender_type"], parent["sender_id"])
        targets = [sender] if sender != me else [
            (r["recipient_type"], r["recipient_id"]) for r in
            tx.all("SELECT * FROM message_recipients WHERE message_id = ?", (reply_to,))]
        channel = "direct" if parent["channel"] == "broadcast" else parent["channel"]
        return channel, parent["ticket_id"], parent["thread_id"], targets
    thread = new_id()
    if to == "human":
        return "human", None, thread, [("human", "human")]
    if to == "all":
        agents = tx.all("SELECT id FROM agents WHERE archived_ms IS NULL")
        return "broadcast", None, thread, [("agent", a["id"]) for a in agents] + [("human", "human")]
    if TICKET_KEY.match(to):
        ticket = tx.one("SELECT id, assignee_agent_id FROM tickets WHERE key = ?", (to,))
        if ticket is None:
            raise NotFound("unknown_ticket", f"No ticket {to}.")
        targets = [("human", "human")]
        if ticket["assignee_agent_id"]:
            targets.append(("agent", ticket["assignee_agent_id"]))
        return "ticket", ticket["id"], f"ticket:{ticket['id']}", targets
    agent = tx.one("SELECT id FROM agents WHERE lower(display_name) = lower(?) AND archived_ms IS NULL", (to,))
    if agent is None:
        raise NotFound("unknown_recipient", f"No agent seat named {to!r}; use a seat name, a ticket key, 'all' or "
                       "'human'.")
    return "direct", None, thread, [("agent", agent["id"])]


def send(tx: Tx, actor: Actor, now: int, *, to: str, body: str, requires_response: bool = False,
         reply_to: str | None = None) -> dict:
    body = redact(body.strip())
    if not body:
        raise Invalid("body_required", "A message needs text.")
    sender_type, sender_id = _me(actor)
    channel, ticket_id, thread, targets = _recipients(tx, actor, to, reply_to)
    targets = [t for t in dict.fromkeys(targets) if t != (sender_type, sender_id)]
    if not targets:
        raise Invalid("no_recipients", "Nobody else would receive this message.")
    if actor.is_agent and channel == "broadcast" and tx.scalar(
            "SELECT 1 FROM messages WHERE sender_id = ? AND channel = 'broadcast' AND created_ms > ?",
            (sender_id, now - BROADCAST_INTERVAL_MS)):
        raise RateLimited("broadcast_limited", "One broadcast per agent every 10 minutes.")
    if actor.is_agent and requires_response and tx.scalar(
            "SELECT COUNT(*) FROM messages m WHERE m.sender_id = ? AND m.requires_response = 1 AND NOT EXISTS"
            " (SELECT 1 FROM message_recipients r WHERE r.message_id = m.id AND r.read_ms IS NOT NULL)", (sender_id,)
    ) >= OPEN_REQUESTS_PER_SENDER:
        raise RateLimited("too_many_open_requests", "Wait for answers to your open requests first.")
    message_id = new_id()
    tx.execute("INSERT INTO messages (id, thread_id, parent_id, channel, ticket_id, sender_type, sender_id,"
               " sender_session_id, body, requires_response, created_ms) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
               (message_id, thread, reply_to, channel, ticket_id, sender_type, sender_id, actor.session_id, body,
                int(requires_response), now))
    tx.conn.executemany("INSERT INTO message_recipients (message_id, recipient_type, recipient_id) VALUES (?, ?, ?)",
                        [(message_id, *target) for target in targets])
    events.emit(tx, "message.sent", "message", message_id, actor,
                {"channel": channel, "thread_id": thread, "recipients": [list(t) for t in targets]}, now)
    return {"message_id": message_id, "thread_id": thread, "channel": channel, "recipients": len(targets)}


def _serialize(row) -> dict:
    return {"id": row["id"], "thread_id": row["thread_id"], "parent_id": row["parent_id"], "channel": row["channel"],
            "ticket": row["ticket_key"], "from": row["sender_name"] or row["sender_type"],
            "trust": "human" if row["sender_type"] == "human" else "agent", "body": row["body"],
            "requires_response": bool(row["requires_response"]), "created_ms": row["created_ms"]}


def inbox(tx: Tx, actor: Actor, now: int, ack: list[str] | None = None, limit: int = 20) -> dict:
    recipient_type, recipient_id = _me(actor)
    if ack:
        tx.execute(f"UPDATE message_recipients SET read_ms = ? WHERE recipient_type = ? AND recipient_id = ?"
                   f" AND message_id IN ({','.join('?' * len(ack))})", (now, recipient_type, recipient_id, *ack))
    rows = tx.all(f"{_SELECT} JOIN message_recipients r ON r.message_id = m.id WHERE r.recipient_type = ?"
                  " AND r.recipient_id = ? AND r.read_ms IS NULL ORDER BY m.created_ms LIMIT ?",
                  (recipient_type, recipient_id, limit))
    tx.execute("UPDATE message_recipients SET surfaced_ms = COALESCE(surfaced_ms, ?) WHERE recipient_type = ?"
               " AND recipient_id = ? AND read_ms IS NULL", (now, recipient_type, recipient_id))
    return {"unread": [_serialize(row) for row in rows], "unread_total": unread_count(tx, recipient_type, recipient_id)}


def unread_count(tx: Tx, recipient_type: str, recipient_id: str) -> int:
    return tx.scalar("SELECT COUNT(*) FROM message_recipients WHERE recipient_type = ? AND recipient_id = ?"
                     " AND read_ms IS NULL", (recipient_type, recipient_id))


def recent(tx: Tx, limit: int = 200) -> list[dict]:
    rows = tx.all(f"{_SELECT} ORDER BY m.created_ms DESC LIMIT ?", (limit,))
    if not rows:
        return []
    reads = {}
    ids = [row["id"] for row in rows]
    for r in tx.all("SELECT r.*, a.display_name FROM message_recipients r LEFT JOIN agents a ON a.id = r.recipient_id"
                    f" WHERE r.message_id IN ({','.join('?' * len(ids))})", ids):
        reads.setdefault(r["message_id"], []).append({"to": r["display_name"] or r["recipient_type"],
                                                      "surfaced_ms": r["surfaced_ms"], "read_ms": r["read_ms"]})
    return [_serialize(row) | {"recipients": reads.get(row["id"], [])} for row in rows]
