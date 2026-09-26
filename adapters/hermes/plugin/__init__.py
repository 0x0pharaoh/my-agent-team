"""Hermes plugin: my-team kanban bridge. Hook names and payloads verified against Hermes 0.21.4."""
import json
import os
import shutil
import subprocess

from .bridge import myteam_events_to_hermes_actions

PLUGIN_STORAGE_KEY = "my-team-kanban-bridge"


def cli_path() -> str:
    return os.environ.get("HERMES_MY_TEAM_CLI") or shutil.which("my-team") or "my-team"


def register(ctx):
    store = ctx.state
    ctx.register_hook("on_session_start", lambda session_id=None, **kw: _on_session_start(store, session_id))
    ctx.register_hook("kanban_task_claimed",
                      lambda task_id=None, **kw: _on_kanban_task(store, "claimed", task_id, kw))
    ctx.register_hook("kanban_task_completed",
                      lambda task_id=None, **kw: _on_kanban_task(store, "completed", task_id, kw))
    ctx.register_hook("kanban_task_blocked",
                      lambda task_id=None, **kw: _on_kanban_task(store, "blocked", task_id, kw))
    ctx.register_hook("on_kanban_dispatch_tick", lambda **kw: _on_tick(store))


def _on_session_start(store, session_id):
    state = _read_state(store)
    if not _project_and_session(state):
        return
    _reconcile(store, state, _seat(state))
    _write_state(store, state)


def _on_tick(store):
    state = _read_state(store)
    if not _project_and_session(state):
        return
    _reconcile(store, state, _seat(state))
    _write_state(store, state)


def _on_kanban_task(store, kind, task_id, kw):
    if not task_id:
        return
    state = _read_state(store)
    pm = _project_and_session(state)
    if pm is None:
        return
    project, session = pm
    mapping = state.get("mapping", {})
    key = _task_to_ticket_key(mapping, task_id)
    if not key:
        return
    handlers = {"claimed": _handle_claimed, "completed": _handle_completed, "blocked": _handle_blocked}
    handlers[kind](project, session, state, key, task_id, kw)
    _write_state(store, state)


def _handle_claimed(project, session, state, key, task_id, kw):
    try:
        call_myteam("ticket_claim", {"key": key}, project, session)
    except RuntimeError as exc:
        if "409" in str(exc):
            _record_block(state, task_id, "claimed elsewhere in my-team")


def _handle_completed(project, session, state, key, task_id, kw):
    call_myteam("ticket_update", {"key": key, "action": "review", "epoch": _epoch(project, session, key),
                                  "summary": kw.get("summary") or ""}, project, session)


def _handle_blocked(project, session, state, key, task_id, kw):
    call_myteam("ticket_update", {"key": key, "action": "block", "epoch": _epoch(project, session, key),
                                  "note": kw.get("reason") or "blocked in Hermes"}, project, session)


def _epoch(project, session, key):
    found = call_myteam("ticket_find", {"key": key}, project, session).get("tickets", [])
    return found[0].get("claim_epoch") if found else None


def _read_state(store) -> dict:
    raw = store.get(PLUGIN_STORAGE_KEY) if store is not None else None
    return json.loads(raw) if isinstance(raw, str) else dict(raw or {})


def _write_state(store, state: dict) -> None:
    if store is not None:
        store.set(PLUGIN_STORAGE_KEY, json.dumps(state, separators=(",", ":")))


def _project_and_session(state: dict) -> tuple[str, str] | None:
    if state.get("project") and state.get("session"):
        return state["project"], state["session"]
    return None


def _seat(state: dict) -> str | None:
    return state.get("_seat_agent_id") or os.environ.get("HERMES_SEAT_AGENT_ID")


def _reconcile(store, state: dict, seat_agent_id: str | None) -> None:
    cursor = state.get("cursor", 0)
    pm = _project_and_session(state)
    if pm is None:
        return
    try:
        found = call_myteam("events_since", {"after": cursor, "limit": 100}, *pm)
    except RuntimeError:
        return
    found_events: list[dict] = found.get("events", [])
    if not found_events:
        return
    state["pending_actions"] = myteam_events_to_hermes_actions(found_events, state.get("mapping", {}),
                                                               seat_agent_id)
    state["cursor"] = max([e.get("id", cursor) for e in found_events] + [cursor])


def _task_to_ticket_key(mapping: dict, task_id: str) -> str | None:
    return next((k for k, v in mapping.items() if v == task_id and not k.startswith("_")), None)


def _record_block(state: dict, task_id: str, reason: str) -> None:
    state["pending_blocks"] = state.get("pending_blocks", []) + [{"task_id": task_id, "reason": reason}]


def call_myteam(op: str, payload: dict | None, project: str, session: str | None) -> dict:
    """Run `my-team call <op> '<json>' --project P [--session S]` and return parsed JSON stdout."""
    args = ["call", op]
    if payload is not None:
        args.append(json.dumps(payload))
    if project:
        args += ["--project", project]
    if session:
        args += ["--session", session]
    proc = subprocess.run([cli_path(), *args], capture_output=True, text=True, timeout=30, check=False)
    if proc.returncode != 0:
        raise RuntimeError(f"my-team call {op!r} failed: {proc.stderr}")
    return json.loads(proc.stdout)
