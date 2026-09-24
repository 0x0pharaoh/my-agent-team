import json
import os
import shlex
import subprocess
from pathlib import Path

from my_team import config
from my_team.installers import cli_path, confirm, copy_skill


def install(yes: bool = False) -> int:
    home = Path(os.environ.get("HOME") or Path.home())
    codex_home = Path(os.environ.get("CODEX_HOME") or home / ".codex")
    cli = str(Path(cli_path()).resolve())
    plan = [
        f"copy my-team skill to {home / '.agents' / 'skills' / 'my-team'}",
        "add Codex MCP server my-team if missing",
        f"merge my-team hooks into {codex_home / 'hooks.json'}",
        "let the my-team daemon start on demand (no OS service)",
    ]
    if not confirm(plan, yes):
        return 1
    copy_skill(home / ".agents" / "skills" / "my-team")
    _add_mcp(cli)
    _write_hooks(codex_home, cli)
    config.save(autostart=True)
    print("\nDone. Trust the my-team hooks once in Codex's /hooks screen, then run $my-team init in a project.")
    return 0


def _add_mcp(cli: str) -> None:
    found = _run(["codex", "mcp", "get", "my-team"], capture_output=True, text=True, check=False)
    if found.returncode == 0:
        return
    _run(["codex", "mcp", "add", "my-team", "--", cli, "mcp"], check=False)


def _run(args: list[str], **kwargs):
    check = kwargs.pop("check", False)
    return subprocess.run(args, check=check, **kwargs)


def _write_hooks(codex_home: Path, cli: str) -> None:
    hooks_path = codex_home / "hooks.json"
    codex_home.mkdir(parents=True, exist_ok=True)
    data = _read_hooks(hooks_path)
    hooks = data.setdefault("hooks", {})
    for event in ("SessionStart", "UserPromptSubmit"):
        hooks[event] = [entry for entry in hooks.get(event, []) if not _ours(entry)]
    hooks["SessionStart"].append({"matcher": "startup|resume|clear|compact", "hooks": [_session_start(cli)]})
    hooks["UserPromptSubmit"].append({"hooks": [PROMPT_HOOK]})
    hooks_path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")


PROMPT_HOOK = {"type": "mcp_tool", "server": "my-team", "tool": "hook_prompt",
               "input": {"session_id": "${session_id}", "cwd": "${cwd}"}, "timeout": 20}


def _read_hooks(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {"hooks": {}}
    return data if isinstance(data.get("hooks"), dict) else {"hooks": {}}


def _session_start(cli: str) -> dict:
    """Codex runs command as a shell string (commandWindows on Windows); there is no exec-form args field."""
    tail = "hook session-start --agent codex"
    windows = f"& '{cli}' {tail}" if " " in cli else f"{cli} {tail}"
    return {"type": "command", "command": f"{shlex.quote(cli)} {tail}", "commandWindows": windows, "timeout": 20,
            "statusMessage": "Loading my-team context"}


def _ours(entry) -> bool:
    handlers = entry.get("hooks", []) if isinstance(entry, dict) else []
    return bool(handlers) and all(
        "--agent codex" in " ".join([str(h.get("command", "")), *map(str, h.get("args", []))])
        or (h.get("type") == "mcp_tool" and h.get("server") == "my-team")
        for h in handlers)
