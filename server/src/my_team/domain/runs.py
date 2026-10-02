import sqlite3

from my_team.actor import SYSTEM, Actor
from my_team.db.engine import Tx
from my_team.domain import events, questions, settings, tickets
from my_team.errors import Conflict, Invalid, NotFound
from my_team.ids import new_id

ACTIVE = ("queued", "running")
EXTEND, REASSIGN, CANCEL = "Extend budget x2", "Reassign", "Cancel ticket"
_SELECT = ("SELECT r.*, t.key AS ticket_key, a.display_name AS agent_name FROM runs r"
           " LEFT JOIN tickets t ON t.id = r.ticket_id JOIN agents a ON a.id = r.agent_id")
_FIELDS = ("id", "pid", "kind", "step_index", "agent_type", "status", "worktree_path", "branch", "max_tokens", "max_seconds",
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
    run_id = new_id()
    try:
        tx.execute("INSERT INTO runs (id, ticket_id, agent_id, agent_type, status, branch, max_tokens, max_seconds,"
                   " created_ms) VALUES (?, ?, ?, ?, 'queued', ?, ?, ?, ?)",
                   (run_id, ticket["id"], ticket["assignee_agent_id"], ticket["agent_type"], f"mt/{ticket['key']}",
                    ticket["max_tokens"] or conf["default_max_tokens"],
                    60 * (ticket["max_minutes"] or conf["default_max_minutes"]), now))
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
    key = run["ticket"]
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
        tickets.block(tx, SYSTEM, key, "run ended without moving the ticket to review", now)
    return get(tx, run_id)


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
