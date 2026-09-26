import json
import os
import sys

from my_team import activation
from my_team.client import DaemonError, DaemonUnavailable, connect
from my_team.context import NOT_INITIALIZED, hook_output, render_context, unavailable

EVENTS = ("session-start", "prompt", "pre-edit")


def output(agent: str, event: str, text: str | None) -> str:
    """Claude Code wants hookSpecificOutput JSON, Hermes wants {"context"}, Codex and OpenCode take plain stdout."""
    if not text:
        return ""
    if agent == "claude-code":
        return hook_output("SessionStart" if event == "session-start" else "UserPromptSubmit", text)
    if agent == "hermes":
        return json.dumps({"context": text})
    return text


def _session(agent: str, native: str | None, cwd: str):
    """One daemon session lookup. Nones mean off/unknown/uninitialized, raises mean daemon down."""
    resolved = activation.resolve(activation.load(), agent, native, cwd)
    if not (resolved["active"] and native):
        return resolved, None, None, None
    client = connect()
    project = client.registry("project_resolve", {"cwd": cwd})["project"]
    if not project:
        return resolved, client, None, None
    session = client.project(project["id"], "session_register",
                             {"agent_type": agent, "native_id": native, "root_path": cwd})
    return resolved, client, project, session


def context_text(agent: str, event: str, payload: dict) -> str | None:
    native, cwd = payload.get("session_id"), payload.get("cwd") or os.getcwd()
    try:
        resolved, client, project, session = _session(agent, native, cwd)
    except (DaemonUnavailable, DaemonError) as exc:
        return unavailable(exc)
    if client is None:
        return None
    if project is None:
        return NOT_INITIALIZED
    full = event == "session-start" or not session["context_sent"]
    try:
        data = client.project(project["id"], "team_context", {"delta": not full}, session["session_id"])
    except (DaemonUnavailable, DaemonError) as exc:
        return unavailable(exc)
    return render_context(data, resolved, full)


def pre_edit_text(agent: str, payload: dict) -> str | None:
    """Fail-open pre-edit guard: the edit_check reason when it says ask, else None. Exit stays 0."""
    try:
        native, cwd = payload.get("session_id"), payload.get("cwd") or os.getcwd()
        _, client, project, session = _session(agent, native, cwd)
        if client is None or project is None:
            return None
        verdict = client.project(project["id"], "edit_check", {"path": payload.get("file_path")},
                                 session["session_id"])
    except Exception:
        return None
    return verdict["reason"] if verdict["decision"] == "ask" else None


def run(event: str, agent: str) -> int:
    payload = json.loads(sys.stdin.read() or "{}")
    if event == "pre-edit":
        text = pre_edit_text(agent, payload)
    else:
        text = output(agent, event, context_text(agent, event, payload))
    if text:
        sys.stdout.reconfigure(encoding="utf-8")
        print(text)
    return 0
