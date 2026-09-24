import json
import os
import sys

from my_team import activation
from my_team.client import DaemonError, DaemonUnavailable, connect
from my_team.context import NOT_INITIALIZED, hook_output, render_context, unavailable

EVENTS = ("session-start", "prompt")


def output(agent: str, event: str, text: str | None) -> str:
    """Claude Code wants hookSpecificOutput JSON, Hermes wants {"context"}, Codex and OpenCode take plain stdout."""
    if not text:
        return ""
    if agent == "claude-code":
        return hook_output("SessionStart" if event == "session-start" else "UserPromptSubmit", text)
    if agent == "hermes":
        return json.dumps({"context": text})
    return text


def context_text(agent: str, event: str, payload: dict) -> str | None:
    native, cwd = payload.get("session_id"), payload.get("cwd") or os.getcwd()
    resolved = activation.resolve(activation.load(), agent, native, cwd)
    if not (resolved["active"] and native):
        return None
    try:
        client = connect()
        project = client.registry("project_resolve", {"cwd": cwd})["project"]
        if not project:
            return NOT_INITIALIZED
        session = client.project(project["id"], "session_register",
                                 {"agent_type": agent, "native_id": native, "root_path": cwd})
        full = event == "session-start" or not session["context_sent"]
        data = client.project(project["id"], "team_context", {"delta": not full}, session["session_id"])
    except (DaemonUnavailable, DaemonError) as exc:
        return unavailable(exc)
    return render_context(data, resolved, full)


def run(event: str, agent: str) -> int:
    payload = json.loads(sys.stdin.read() or "{}")
    text = output(agent, event, context_text(agent, event, payload))
    if text:
        sys.stdout.reconfigure(encoding="utf-8")
        print(text)
    return 0
