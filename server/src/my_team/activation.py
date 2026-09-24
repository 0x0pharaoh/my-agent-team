import json
import os
import time
from collections.abc import Callable
from pathlib import Path

from filelock import FileLock

from my_team.clock import now_ms
from my_team.paths import private_config_dir

SCOPES = ("global", "directory", "session", "one-time")
PRUNE_AFTER_MS = 7 * 24 * 3600_000


def _path() -> Path:
    return private_config_dir() / "activation.json"


def empty() -> dict:
    return {"global": False, "directories": {}, "sessions": {}}


def load() -> dict:
    try:
        state = json.loads(_path().read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError):
        return empty()
    return empty() | {k: state[k] for k in ("global", "directories", "sessions") if k in state}


def normalize_dir(path: str | os.PathLike) -> str:
    return os.path.normcase(os.path.realpath(path))


def session_key(agent_type: str, native_id: str) -> str:
    return f"{agent_type}:{native_id}"


def resolve(state: dict, agent_type: str, native_id: str | None, cwd: str | os.PathLike) -> dict:
    if native_id:
        entry = state["sessions"].get(session_key(agent_type, native_id))
        if entry:
            scope = "one-time" if entry.get("task") else "session"
            return {"active": entry["state"] == "on", "scope": scope, "task": entry.get("task")}
    here = normalize_dir(cwd)
    matches = [(path, value) for path, value in state["directories"].items()
               if here == path or here.startswith(path.rstrip(os.sep) + os.sep)]
    if matches:
        path, value = max(matches, key=lambda item: len(item[0]))
        return {"active": value == "on", "scope": "directory", "path": path, "task": None}
    return {"active": bool(state["global"]), "scope": "global" if state["global"] else "default", "task": None}


def _write(state: dict) -> None:
    path = _path()
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(state, indent=1, sort_keys=True), encoding="utf-8")
    for attempt in range(10):
        try:
            os.replace(tmp, path)
            return
        except PermissionError:
            if attempt == 9:
                raise
            time.sleep(0.1)


def update(change: Callable[[dict], None]) -> dict:
    with FileLock(str(_path()) + ".lock", timeout=10):
        state = load()
        now = now_ms()
        state["sessions"] = {k: v for k, v in state["sessions"].items() if now - v.get("seen_ms", now) < PRUNE_AFTER_MS}
        change(state)
        _write(state)
        return state


def set_scope(scope: str, on: bool, *, cwd: str | os.PathLike, agent_type: str | None = None,
              native_id: str | None = None, task: str | None = None, path: str | None = None) -> dict:
    if scope not in SCOPES:
        raise ValueError(f"scope must be one of {', '.join(SCOPES)}")

    def change(state: dict) -> None:
        if scope == "global":
            state["global"] = on
        elif scope == "directory":
            state["directories"][normalize_dir(path or cwd)] = "on" if on else "off"
        else:
            if not (agent_type and native_id):
                raise ValueError("session and one-time scopes need the agent session id")
            key = session_key(agent_type, native_id)
            if scope == "one-time" and not on:
                state["sessions"].pop(key, None)
                return
            entry = {"state": "on" if on else "off", "seen_ms": now_ms()}
            if scope == "one-time":
                entry["task"] = task or "one-time task"
            state["sessions"][key] = entry

    return update(change)
