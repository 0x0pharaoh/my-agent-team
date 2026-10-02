"""my-team install for Hermes — idempotent installation into a Hermes profile."""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path

from my_team.config import load as config_load
from my_team.config import save as config_save
from my_team.installers import cli_path as my_team_cli_path
from my_team.installers import confirm, copy_skill

HERMES_MCP_SERVER = "my-team"
HERMES_HOOKS_AGENT = "hermes"


def hermes_home() -> Path:
    env = os.environ.get("HERMES_HOME")
    if env:
        return Path(env).expanduser().resolve()
    localappdata = os.environ.get("LOCALAPPDATA")
    if localappdata:
        return Path(localappdata) / "hermes"
    return Path.home() / ".hermes"


def _skill_dest(home: Path) -> Path:
    return home / "skills" / "software-development" / "my-team"


def config_path(home: Path) -> Path:
    return home / "config.yaml"


def _hermes_exe() -> str | None:
    """Standard Hermes exe path. No sys.argv[0] fallback (that resolves to my-team itself)."""
    localappdata = os.environ.get("LOCALAPPDATA")
    if localappdata:
        exe = Path(localappdata) / "hermes" / "bin" / "hermes.exe"
        if exe.exists():
            return str(exe)
    return None


def _hermes_cli() -> str:
    for cand in (shutil.which("hermes"), _hermes_exe()):
        if cand:
            return cand
    raise FileNotFoundError(
        "hermes CLI not found. Install Hermes or set HERMES_HOME."
    )


def _install_skill(home: Path) -> None:
    dst = _skill_dest(home)
    if copy_skill(dst):
        print(f"installed skill: {dst}")
    else:
        print(f"skill already installed: {dst}")


def _hooks_registered(text: str) -> bool:
    return "--agent hermes" in text


def _hooks_text_block(myteam_cli: str) -> str:
    """Hook commands as JSON strings: Hermes splits with shlex posix=False, so one quote layer survives."""
    cmd_prompt = f'"{myteam_cli}" hook prompt --agent hermes'
    cmd_tool = f'"{myteam_cli}" hook tool --agent hermes'
    cmd_pre_edit = f'"{myteam_cli}" hook pre-edit --agent hermes'
    return (
        "hooks:\n"
        f"  pre_llm_call:\n"
        f"    - command: {json.dumps(cmd_prompt)}\n"
        f"  pre_tool_call:\n"
        f'    - matcher: "mcp__my_team__.*"\n'
        f"      command: {json.dumps(cmd_tool)}\n"
        f'    - matcher: "write_file|patch"\n'
        f"      command: {json.dumps(cmd_pre_edit)}\n"
    )


def _mcp_registered(home: Path) -> bool:
    """True when config.yaml already lists my-team under mcp_servers (checked per block, not substring)."""
    try:
        text = config_path(home).read_text(encoding="utf-8")
    except OSError:
        return False
    section = re.search(r"(?m)^mcp_servers\s*:", text)
    if not section:
        return False
    rest = text[section.end():]
    dedented = re.search(r"(?m)^\S", rest)
    return bool(re.search(r"(?m)^\s+my-team\s*:", rest[:dedented.start()] if dedented else rest))


def _ensure_mcp(home: Path, hermes_cli: str, myteam_cli: str) -> None:
    """Register the MCP server via `hermes mcp add`, attached to the console for its prompts."""
    if _mcp_registered(home):
        print("MCP server my-team already registered")
        return
    cmd = [hermes_cli, "mcp", "add", HERMES_MCP_SERVER, "--command", myteam_cli,
           "--env", "MY_TEAM_AGENT_TYPE=hermes", "--args", "mcp"]
    print(f"running: {' '.join(cmd)}")
    try:
        proc = subprocess.run(cmd, check=False)
    except (FileNotFoundError, OSError) as exc:
        print(f"hermes CLI not found: {exc}")
        return
    if _mcp_registered(home):
        print("MCP server my-team registered via hermes CLI")
    else:
        print(f"hermes mcp add did not register my-team (rc={proc.returncode}); add it manually")


def _ensure_hooks(home: Path, hermes_cli: str, myteam_cli: str) -> None:
    """Append shell hooks to config.yaml directly; a `hermes hooks` CLI does not exist."""
    cfg_path = config_path(home)
    _merge_hooks_text(cfg_path, myteam_cli)


def _merge_hooks_text(cfg_path: Path, myteam_cli: str) -> None:
    cfg_path.parent.mkdir(parents=True, exist_ok=True)
    text = cfg_path.read_text(encoding="utf-8") if cfg_path.exists() else ""
    if _hooks_registered(text):
        print("Hermes hooks for my-team already registered")
        return
    if re.search(r"^hooks\s*:", text, re.MULTILINE):
        snippet = _hooks_text_block(myteam_cli)
        print(f"--- paste into your config.yaml under `hooks:` ---\n{snippet}---\n")
        return
    if text.strip():
        backup = str(cfg_path) + ".my-team.bak"
        shutil.copy2(cfg_path, backup)
        print(f"backed up config.yaml to {backup}")
    with open(cfg_path, "a", encoding="utf-8") as f:
        f.write("\n" + _hooks_text_block(myteam_cli) + "\n")
    print("Hermes hooks for my-team appended to config.yaml")


def _ensure_autostart() -> None:
    cfg = config_load()
    if cfg.get("autostart"):
        print("autostart already enabled")
        return
    config_save(autostart=True)
    print("autostart enabled in my-team config")


def _build_plan(home: Path, hermes_cli: str, myteam_cli: str) -> list[str]:
    return [
        f"install skill to {_skill_dest(home)}",
        f"register MCP server ({hermes_cli} mcp add {HERMES_MCP_SERVER} ...)",
        "append pre_llm_call + pre_tool_call hooks to config.yaml",
        "enable autostart in my-team config",
    ]


def install(yes: bool) -> int:
    home = hermes_home()
    hermes_cli = _hermes_cli()
    myteam_cli = my_team_cli_path()
    if not confirm(_build_plan(home, hermes_cli, myteam_cli), yes):
        return 1
    _install_skill(home)
    _ensure_mcp(home, hermes_cli, myteam_cli)
    _ensure_hooks(home, hermes_cli, myteam_cli)
    _ensure_autostart()
    return 0
