import json
import os
import re
import sys

from my_team import activation
from my_team.client import DaemonError, DaemonUnavailable, connect
from my_team.context import NOT_INITIALIZED, hook_output, render_context, unavailable

EVENTS = ("session-start", "prompt", "pre-edit", "tool")
_PATCH_FILE = re.compile(r"^\*\*\* (?:Update|Add|Delete) File: (.+)$", re.MULTILINE)


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
                             {"agent_type": agent, "native_id": native, "root_path": cwd, "via": "hook"})
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
        for path in _edit_paths(payload) or [None]:
            verdict = client.project(project["id"], "edit_check", {"path": path}, session["session_id"])
            if verdict["decision"] == "ask":
                return verdict["reason"]
    except Exception:
        return None
    return None


def _edit_paths(payload: dict) -> list:
    if payload.get("file_path"):
        return [payload["file_path"]]
    tool_input = payload.get("tool_input") or {}
    if tool_input.get("path"):
        return [tool_input["path"]]
    command = tool_input.get("command")
    if isinstance(command, str) and "*** Begin Patch" in command:
        return [m.group(1).strip() for m in _PATCH_FILE.finditer(command)]
    return []


def run(event: str, agent: str) -> int:
    payload = json.loads(sys.stdin.read() or "{}")
    if event == "pre-edit":
        text = pre_edit_text(agent, payload)
        if agent == "hermes" and text:
            text = json.dumps({"action": "approve", "message": text})
        elif agent == "codex" and text and os.environ.get("MY_TEAM_CODEX_DENY_UNCLAIMED") == "1":
            text = json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse",
                                                      "permissionDecision": "deny",
                                                      "permissionDecisionReason": text}})
    elif event == "tool":
        session = payload.get("session_id")
        text = json.dumps({"action": "modify", "args": {"session": session}}) if session else None
    else:
        text = output(agent, event, context_text(agent, event, payload))
    if text:
        sys.stdout.reconfigure(encoding="utf-8")
        print(text)
    return 0
