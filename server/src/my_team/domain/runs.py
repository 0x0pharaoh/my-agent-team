import json
import sqlite3

from my_team.actor import SYSTEM, Actor
from my_team.db.engine import Tx
from my_team.domain import events, questions, sessions, settings, tickets
from my_team.errors import Conflict, Invalid, NotFound
from my_team.ids import new_id

ACTIVE = ("queued", "running")
EXTEND, REASSIGN, CANCEL = "Extend budget x2", "Reassign", "Cancel ticket"
REPLY_MAX_TOKENS, REPLY_MAX_SECONDS, REPLY_GRACE_MS = 60_000, 300, 30_000
_SELECT = ("SELECT r.*, t.key AS ticket_key, a.display_name AS agent_name FROM runs r"
           " LEFT JOIN tickets t ON t.id = r.ticket_id JOIN agents a ON a.id = r.agent_id")
_FIELDS = ("id", "pid", "message_id", "kind", "step_index", "agent_type", "status", "worktree_path", "branch", "max_tokens", "max_seconds",
           "tokens", "cost_usd", "summary", "error", "native_session_id", "session_id", "created_ms", "started_ms",
           "ended_ms")


def _serialize(row) -> dict:
    return {**{key: row[key] for key in _FIELDS}, "ticket": row["ticket_key"], "agent": row["agent_name"]}


def get(tx: Tx, run_id: str) -> dict:
    row = tx.one(f"{_SELECT} WHERE r.id = ?", (run_id,))
    if row is None:
        raise NotFound("unknown_run", "No such run.")
    return _serialize(row)


def listing(tx: Tx, key: str | None = None, active_only: bool = False, limit: int = 50) -> list[dict]:
    clauses, params = [], []
    if key:
        clauses.append("t.key = ?")
        params.append(key)
    if active_only:
        clauses.append(f"r.status IN {ACTIVE}")
    where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
    return [_serialize(row) for row in tx.all(f"{_SELECT}{where} ORDER BY r.created_ms DESC LIMIT ?", (*params, limit))]


def _queue(tx: Tx, ticket, now: int, actor: Actor) -> dict:
    conf = settings.get(tx)
    steps = json.loads(ticket["workflow"])
    step = steps[ticket["step"]] if ticket["step"] < len(steps) else {}
    run_id = new_id()
    try:
        tx.execute("INSERT INTO runs (id, ticket_id, step_index, agent_id, agent_type, status, branch, max_tokens,"
                   " max_seconds, created_ms) VALUES (?, ?, ?, ?, ?, 'queued', ?, ?, ?, ?)",
                   (run_id, ticket["id"], ticket["step"], ticket["assignee_agent_id"], ticket["agent_type"],
                    f"mt/{ticket['key']}",
                    step.get("max_tokens") or ticket["max_tokens"] or conf["default_max_tokens"],
                    60 * (step.get("max_minutes") or ticket["max_minutes"] or conf["default_max_minutes"]), now))
    except sqlite3.IntegrityError:
        raise Conflict("run_active", f"{ticket['key']} already has an active run.") from None
    events.emit(tx, "run.queued", "run", run_id, actor, {"ticket": ticket["key"], "agent_type": ticket["agent_type"]},
                now)
    return get(tx, run_id)


_RUNNABLE = ("SELECT t.*, a.agent_type FROM tickets t JOIN agents a ON a.id = t.assignee_agent_id"
             " WHERE t.status = 'ready'")


def start(tx: Tx, actor: Actor, key: str, now: int) -> dict:
    ticket = tx.one(f"{_RUNNABLE} AND t.key = ?", (key,))
    if ticket is None:
        raise Invalid("not_runnable", f"{key} must be Ready and assigned to an agent before it can run.")
    return _queue(tx, ticket, now, actor)


def schedule(tx: Tx, now: int, agent_types: set[str], limit: int) -> list[dict]:
    """Queue runs for Ready, assigned, unblocked tickets of runnable agent types, up to the parallel limit."""
    free = limit - tx.scalar(f"SELECT COUNT(*) FROM runs WHERE status IN {ACTIVE}")
    if free <= 0 or not agent_types:
        return []
    placeholders = ",".join("?" * len(agent_types))
    candidates = tx.all(
        f"{_RUNNABLE} AND a.agent_type IN ({placeholders})"
        f" AND NOT EXISTS (SELECT 1 FROM runs r WHERE r.ticket_id = t.id AND r.status IN {ACTIVE})"
        " AND NOT EXISTS (SELECT 1 FROM ticket_links l JOIN tickets b ON b.id = l.from_id WHERE l.to_id = t.id"
        " AND l.type = 'blocks' AND l.overridden_ms IS NULL AND b.status NOT IN ('done', 'cancelled'))"
        " ORDER BY t.priority, t.seq LIMIT ?", (*sorted(agent_types), free))
    return [_queue(tx, ticket, now, SYSTEM) for ticket in candidates]


def schedule_replies(tx: Tx, now: int, agent_types: set[str], limit: int) -> list[dict]:
    """One reply run per seat that has unanswered human messages and no live session to see them."""
    free = limit - tx.scalar(f"SELECT COUNT(*) FROM runs WHERE status IN {ACTIVE}")
    if free <= 0 or not agent_types:
        return []
    placeholders = ",".join("?" * len(agent_types))
    seats = tx.all(
        "SELECT a.id AS agent_id, a.agent_type, MIN(m.id) AS message_id FROM messages m"
        " JOIN message_recipients r ON r.message_id = m.id AND r.recipient_type = 'agent' AND r.read_ms IS NULL"
        " JOIN agents a ON a.id = r.recipient_id"
        f" WHERE m.sender_type = 'human' AND m.requires_response = 1 AND m.created_ms < ? AND a.agent_type IN ({placeholders})"
        " AND NOT EXISTS (SELECT 1 FROM runs x WHERE x.message_id = m.id)"
        f" AND NOT EXISTS (SELECT 1 FROM runs x WHERE x.agent_id = a.id AND x.status IN {ACTIVE})"
        " AND NOT EXISTS (SELECT 1 FROM sessions s WHERE s.agent_id = a.id AND s.ended_ms IS NULL"
        " AND s.last_heartbeat_ms > ?) GROUP BY a.id LIMIT ?",
        (now - REPLY_GRACE_MS, *sorted(agent_types), now - sessions.OFFLINE_AFTER_MS, free))
    queued_runs = []
    for seat in seats:
        run_id = new_id()
        tx.execute("INSERT INTO runs (id, kind, agent_id, agent_type, message_id, status, max_tokens, max_seconds,"
                   " created_ms) VALUES (?, 'reply', ?, ?, ?, 'queued', ?, ?, ?)",
                   (run_id, seat["agent_id"], seat["agent_type"], seat["message_id"], REPLY_MAX_TOKENS,
                    REPLY_MAX_SECONDS, now))
        events.emit(tx, "run.queued", "run", run_id, SYSTEM, {"kind": "reply", "agent_type": seat["agent_type"]}, now)
        queued_runs.append(get(tx, run_id))
    return queued_runs


def queued(tx: Tx) -> list[dict]:
    return [_serialize(row) for row in tx.all(f"{_SELECT} WHERE r.status = 'queued' ORDER BY r.created_ms")]


def started(tx: Tx, run_id: str, now: int, pid: int, worktree: str, native_session_id: str | None) -> None:
    tx.execute("UPDATE runs SET status = 'running', pid = ?, worktree_path = ?, native_session_id = ?, started_ms = ?"
               " WHERE id = ?", (pid, worktree, native_session_id, now, run_id))
    events.emit(tx, "run.started", "run", run_id, SYSTEM, {"ticket": get(tx, run_id)["ticket"]}, now)


def progress(tx: Tx, run_id: str, now: int, tokens: int, cost: float | None, native_session_id: str | None) -> None:
    run = get(tx, run_id)
    native = native_session_id or run["native_session_id"]
    session = tx.scalar("SELECT id FROM sessions WHERE agent_type = ? AND native_session_id = ?",
                        (run["agent_type"], native)) if native else None
    tx.execute("UPDATE runs SET tokens = ?, cost_usd = COALESCE(?, cost_usd), native_session_id = ?,"
               " session_id = COALESCE(?, session_id) WHERE id = ?", (tokens, cost, native, session, run_id))
    events.emit(tx, "run.progress", "run", run_id, SYSTEM,
                {"ticket": run["ticket"], "tokens": tokens, "max_tokens": run["max_tokens"]}, now)


def finish(tx: Tx, run_id: str, now: int, status: str, *, summary: str | None = None, error: str | None = None) -> dict:
    run = get(tx, run_id)
    if run["status"] not in ACTIVE:
        return run
    tx.execute("UPDATE runs SET status = ?, summary = ?, error = ?, ended_ms = ? WHERE id = ?",
               (status, (summary or "")[:4000] or None, (error or "")[:2000] or None, now, run_id))
    events.emit(tx, "run.finished", "run", run_id, SYSTEM, {"ticket": run["ticket"], "status": status}, now)
    if run["kind"] == "reply":
        return get(tx, run_id)
    key = run["ticket"]
    ticket = tx.one("SELECT * FROM tickets WHERE key = ?", (key,))
    if status == "budget_exhausted" and ticket["status"] == "in_review":
        status = "succeeded"  # the agent finished its step before the meter tipped over; don't ask about it
    if status == "budget_exhausted":
        tickets.block(tx, SYSTEM, key, "budget_exhausted", now)
        elapsed = (now - (run["started_ms"] or now)) // 60_000
        asked = questions.ask(tx, SYSTEM, now, kind="decision", ticket=key, recommendation=EXTEND,
                              options=[EXTEND, REASSIGN, CANCEL],
                              prompt=f"{key}: the {run['agent']} run hit its budget ({run['tokens']}/"
                                     f"{run['max_tokens']} tokens, {elapsed}/{run['max_seconds'] // 60} min). "
                                     "Its work so far is in the worktree. What next?")
        tx.execute("UPDATE questions SET run_id = ? WHERE id = ?", (run_id, asked["id"]))
    elif status != "succeeded":
        tickets.block(tx, SYSTEM, key, f"run {status}: {(error or summary or '')[:200]}".rstrip(": "), now)
    else:
        steps = json.loads(ticket["workflow"])
        if ticket["status"] == "in_review" and ticket["step"] + 1 < len(steps):
            _next_step(tx, ticket, steps, summary, now)
        else:
            tickets.block(tx, SYSTEM, key, "run ended without moving the ticket to review", now)
    return get(tx, run_id)


def _next_step(tx: Tx, ticket, steps: list[dict], summary: str | None, now: int) -> None:
    """Pipeline handoff: the reviewed step's summary stays on the ticket and the next step's agent gets it Ready."""
    step = ticket["step"] + 1
    agent_id = sessions.ensure_seat(tx, steps[step]["agent_type"], now)
    tx.execute("UPDATE tickets SET status = 'ready', step = ?, assignee_agent_id = ?, changes_requested = 0,"
               " implementation_summary = COALESCE(implementation_summary, ?), version = version + 1, updated_ms = ?"
               " WHERE id = ?", (step, agent_id, summary, now, ticket["id"]))
    events.emit(tx, "ticket.transitioned", "ticket", ticket["id"], SYSTEM,
                {"key": ticket["key"], "from": "in_review", "to": "ready", "action": "pipeline_next",
                 "note": f"step {step + 1}/{len(steps)}: {steps[step]['name']}"}, now)
    events.emit(tx, "ticket.assigned", "ticket", ticket["id"], SYSTEM, {"key": ticket["key"], "agent_id": agent_id}, now)


def set_workflow(tx: Tx, actor: Actor, key: str, steps: list[dict], now: int, stall_cutoff: int) -> dict:
    """Define a step pipeline; the first step's agent gets the ticket and later steps follow on review."""
    tx.execute("UPDATE tickets SET workflow = ?, step = 0, version = version + 1, updated_ms = ? WHERE key = ?",
               (json.dumps(steps), now, key))
    if steps:
        tickets.assign(tx, actor, key, sessions.ensure_seat(tx, steps[0]["agent_type"], now), now, stall_cutoff)
    events.emit(tx, "ticket.edited", "ticket", tickets.get(tx, key, stall_cutoff)["id"], actor,
                {"key": key, "fields": ["workflow"]}, now)
    return tickets.get(tx, key, stall_cutoff)


def resumable(tx: Tx, run_id: str) -> str | None:
    """The agent session to continue: the same ticket's previous run, if it stopped only for its budget."""
    run = tx.one("SELECT ticket_id, agent_type, step_index FROM runs WHERE id = ?", (run_id,))
    prev = tx.one("SELECT native_session_id, status, agent_type, step_index FROM runs WHERE ticket_id = ? AND id <> ?"
                  " ORDER BY created_ms DESC LIMIT 1", (run["ticket_id"], run_id))
    if prev and prev["status"] == "budget_exhausted" and (prev["agent_type"], prev["step_index"]) == (
            run["agent_type"], run["step_index"]):
        return prev["native_session_id"]
    return None


def on_answer(tx: Tx, actor: Actor, question_id: str, answer: str, now: int, stall_cutoff: int) -> None:
    """Apply the human's choice on a budget decision; other questions are untouched."""
    row = tx.one("SELECT q.run_id, q.status, r.max_tokens, r.max_seconds, t.key FROM questions q"
                 " JOIN runs r ON r.id = q.run_id JOIN tickets t ON t.id = r.ticket_id WHERE q.id = ?", (question_id,))
    if row is None or row["status"] != "answered":
        return
    key, blocked = row["key"], tickets.get(tx, row["key"], stall_cutoff)["status"] == "blocked"
    if answer == EXTEND:
        tx.execute("UPDATE tickets SET max_tokens = ?, max_minutes = ? WHERE key = ?",
                   (row["max_tokens"] * 2, row["max_seconds"] * 2 // 60, key))
        if blocked:
            tickets.transition(tx, actor, key, "unblock", now, stall_cutoff)
    elif answer == REASSIGN:
        if blocked:
            tickets.transition(tx, actor, key, "unblock", now, stall_cutoff)
        tickets.assign(tx, actor, key, None, now, stall_cutoff)
    elif answer == CANCEL:
        tickets.transition(tx, actor, key, "cancel", now, stall_cutoff)


def orphaned(tx: Tx) -> list[dict]:
    return [_serialize(row) for row in tx.all(f"{_SELECT} WHERE r.status IN {ACTIVE}")]
