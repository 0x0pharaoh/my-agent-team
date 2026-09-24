"""my-team install hermes — idempotently configures a Hermes profile for my-team.

Honours HERMES_HOME (default %LOCALAPPDATA%\\hermes on Windows). Shows a plan via
confirm() first. Prefers Hermes's own CLI (hermes mcp add, hermes hooks …) over editing
config.yaml; edits YAML only if no CLI exists, and only with a clear diff.

Steps
------
a. Install the skill under $HERMES_HOME/skills/software-development/my-team.
b. Add the MCP server ``my-team`` → ``<cli_path()> mcp``.
c. Add shell hooks ``on_session_start`` → ``hook session-start --agent hermes`` and
   ``pre_llm_call`` → ``hook prompt --agent hermes``.
d. Enable autostart via my_team.config.save(autostart=True).
e. Explain the hook consent step.
"""

from __future__ import annotations

import os
import shlex as _shlex
import shutil
import subprocess
import sys
from pathlib import Path

import yaml

from my_team.config import load
from my_team.config import port as config_port
from my_team.config import save as config_save
from my_team.hook import context_text
from my_team.installers import cli_path as my_team_cli_path
from my_team.installers import confirm, skill_source

HERMES_SKILL_CATEGORY = "software-development"
HERMES_SKILL_NAME = "my-team"
HERMES_MCP_SERVER = "my-team"
HERMES_HOOKS_AGENT = "hermes"

HOOK_CONSENT_HINT = (
    "my-team injects a [my-team] context block into every Hermes prompt via the pre_llm_call "
    "hook (hermes hook prompt --agent hermes) and registers the session via on_session_start. "
    "The first time a hook fires, Hermes shows a consent prompt; after you accept (or set "
    "hooks_auto_accept) the shell command is allowed to run."
)


def _hermes_home() -> Path:
    env = os.environ.get("HERMES_HOME")
    if env:
        return Path(env)
    localappdata = os.environ.get("LOCALAPPDATA")
    if localappdata:
        return Path(localappdata) / "hermes"
    return Path.home() / ".hermes"


def _skill_dest(home: Path) -> Path:
    return home / "skills" / HERMES_SKILL_CATEGORY / HERMES_SKILL_NAME


def _config_path(home: Path) -> Path:
    return home / "config.yaml"


def _hermes_exe() -> Path:
    """Locate the hermes executable in the install tree."""
    localappdata = os.environ.get("LOCALAPPDATA", "")
    if localappdata:
        base = Path(localappdata) / "hermes" / "hermes-agent"
    else:
        base = Path.home() / ".hermes" / "hermes-agent"
    for rel in ("bin/hermes", "venv/Scripts/hermes.exe", "hermes"):
        candidate = base / rel
        if candidate.exists():
            return candidate
    return Path(sys.argv[0])


def _hermes_cli() -> str:
    """Path to the hermes CLI executable (for `hermes mcp add`, `hermes hooks`)."""
    return str(shutil.which("hermes") or _hermes_exe())


def install(yes: bool) -> int:
    home = _hermes_home()
    hermes_cli = _hermes_cli()
    myteam_cli = my_team_cli_path()

    plan = _build_plan(home, hermes_cli, myteam_cli)
    if not yes:
        ok = confirm(plan.splitlines(), yes=False)
        if not ok:
            return 1

    _ensure_home(home)
    _install_skill(home)
    _ensure_mcp(home, hermes_cli, myteam_cli)
    _ensure_hooks(home, hermes_cli, myteam_cli)
    _ensure_autostart()

    return 0


# ---- internal helpers ----

def _ensure_home(home: Path) -> None:
    home.mkdir(parents=True, exist_ok=True)


def _install_skill(home: Path) -> None:
    src = skill_source()
    dst = _skill_dest(home)
    if dst.exists() and _skill_dir_identical(src, dst):
        print(f"skill already installed: {dst}")
        return
    dst.parent.mkdir(parents=True, exist_ok=True)
    if dst.exists():
        shutil.rmtree(dst)
    shutil.copytree(src, dst)
    print(f"skill installed: {dst}")


def _skill_dir_identical(a: Path, b: Path) -> bool:
    a_files = {p.relative_to(a).as_posix() for p in a.rglob("*") if p.is_file()}
    b_files = {p.relative_to(b).as_posix() for p in b.rglob("*") if p.is_file()}
    return a_files == b_files


def _ensure_mcp(home: Path, hermes_cli: str, myteam_cli: str) -> None:
    """Add the my-team MCP server. Prefer `hermes mcp add`; fall back to editing config.yaml."""
    cfg = _read_config(home)
    if HERMES_MCP_SERVER in cfg.get("mcp_servers", {}):
        print("MCP server my-team already registered")
        return
    cmd = [hermes_cli, "mcp", "add", HERMES_MCP_SERVER, "--command", myteam_cli, "--args", "mcp"]
    for key, val in _mcp_env():
        cmd.extend(["--env", f"{key}={val}"])
    print(f"running: {' '.join(_shlex_quote(a) for a in cmd)}")
    proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if proc.returncode == 0:
        print("MCP server my-team added via hermes CLI")
        return
    print(f"hermes mcp add failed (rc={proc.returncode}); falling back to config.yaml edit")
    if proc.stderr:
        print(f"stderr: {proc.stderr}")
    _merge_mcp_config(home, myteam_cli)


def _mcp_env() -> list:
    return [("MY_TEAM_AGENT", "hermes"), ("MY_TEAM_PORT", str(config_port()))]


def _merge_mcp_config(home: Path, myteam_cli: str) -> None:
    cfg = _read_config(home)
    mcps = dict(cfg.get("mcp_servers", {}))
    mcps[HERMES_MCP_SERVER] = {
        "command": myteam_cli,
        "args": ["mcp"],
        "env": dict(_mcp_env()),
    }
    cfg["mcp_servers"] = mcps
    _write_config_with_diff(home, cfg, "mcp_servers")


def _ensure_hooks(home: Path, hermes_cli: str, myteam_cli: str) -> None:
    """Add on_session_start + pre_llm_call shell hooks.

    Hermes v0.21.4 ships ``hooks list/test/revoke/remove`` only — no ``hooks add`` — so we
    always edit config.yaml directly (the CLI path is kept for future-proofing).
    """
    cfg = _read_config(home)
    if _hooks_entry_exists(cfg.get("hooks", {})):
        print("Hermes hooks for my-team already registered")
        return
    # Try CLI anyway (future-proof)
    for subcmd in ("session-start", "prompt"):
        cmd = [hermes_cli, "hooks", subcmd, "--agent", HERMES_HOOKS_AGENT]
        proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
        if proc.returncode == 0:
            print(f"hook {subcmd} added via hermes CLI")
    cfg = _read_config(home)
    if _hooks_entry_exists(cfg.get("hooks", {})):
        _write_config(home, cfg)
        return
    # fallback: edit config.yaml
    print("no hermes hooks-add CLI; editing config.yaml")
    _merge_hooks_config(home, myteam_cli)


def _hooks_entry_exists(hooks: dict) -> bool:
    on_start = hooks.get("on_session_start", [])
    pre_llm = hooks.get("pre_llm_call", [])
    has_start = any("hook session-start --agent hermes" in (e.get("command") or "") for e in on_start)
    has_prompt = any("hook prompt --agent hermes" in (e.get("command") or "") for e in pre_llm)
    return has_start and has_prompt


def _merge_hooks_config(home: Path, myteam_cli: str) -> None:
    cfg = _read_config(home)
    hooks = dict(cfg.get("hooks", {}))
    hooks["on_session_start"] = _dedup_hook_entries(hooks.get("on_session_start", []), [
        {"command": myteam_cli, "args": ["hook", "session-start", "--agent", HERMES_HOOKS_AGENT], "description": "my-team session start"},
    ])
    hooks["pre_llm_call"] = _dedup_hook_entries(hooks.get("pre_llm_call", []), [
        {"command": myteam_cli, "args": ["hook", "prompt", "--agent", HERMES_HOOKS_AGENT], "description": "my-team prompt context injection"},
    ])
    cfg["hooks"] = hooks
    _write_config_with_diff(home, cfg, "hooks")


def _dedup_hook_entries(existing: list, new_entries: list) -> list:
    seen = {(e.get("command"), tuple(e.get("args") or ())) for e in existing}
    out = list(existing)
    for ne in new_entries:
        key = (ne.get("command"), tuple(ne.get("args") or ()))
        if key not in seen:
            out.append(ne)
            seen.add(key)
    return out


def _ensure_autostart() -> None:
    cfg = load()
    if cfg.get("autostart"):
        print("autostart already enabled")
        return
    config_save(autostart=True)
    print("autostart enabled")


def _read_config(home: Path) -> dict:
    path = _config_path(home)
    if not path.exists():
        return {}
    with open(path, encoding="utf-8") as f:
        data = yaml.safe_load(f)
    return data if data else {}


def _write_config(home: Path, cfg: dict) -> None:
    path = _config_path(home)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        yaml.safe_dump(cfg, f, sort_keys=False, allow_unicode=True)


def _write_config_with_diff(home: Path, cfg: dict, section: str) -> None:
    old = _read_config(home)
    old_section = old.get(section, {})
    new_section = cfg.get(section, {})
    if old_section == new_section and section in old and old_section:
        print(f"config.yaml {section} unchanged")
        return
    print(f"--- config.yaml diff ({section}) ---")
    _print_dict_diff(old_section, new_section)
    print("-------------------------------------")
    _write_config(home, cfg)
    print(f"config.yaml updated ({section})")


def _print_dict_diff(old: dict, new: dict) -> None:
    if not old:
        for k, v in new.items():
            _print_val("+", k, v)
        return
    for k, v in new.items():
        if k not in old:
            _print_val("+", k, v)
        elif old[k] != v:
            _print_val("~", k, f"{old[k]} -> {v}")
    for k, v in old.items():
        if k not in new:
            _print_val("-", k, v)


def _print_val(prefix: str, key: str, val: object) -> None:
    if isinstance(val, dict):
        print(f"{prefix} {key}:")
        for ck, cv in val.items():
            print(f"   {ck}: {cv}")
    elif isinstance(val, list):
        print(f"{prefix} {key}:")
        for item in val:
            print(f"   - {item}")
    else:
        print(f"{prefix} {key}: {val}")


def _build_plan(home: Path, hermes_cli: str, myteam_cli: str) -> str:
    skill_dest = _skill_dest(home)
    ctx_txt = ""
    try:
        ctx_txt = context_text(HERMES_HOOKS_AGENT, "session-start", {})
    except Exception:  # noqa: BLE001 — catch all errors from an external call we want to suppress
        ctx_txt = "(context_text unavailable in dry-run)"
    safe_ctx = ctx_txt or ""
    lines = [
        f"Hermes home: {home}",
        f"My-team CLI : {myteam_cli}",
        f"Hermes CLI : {hermes_cli}",
        "",
        "a. Install skill",
        f"     src: {skill_source()}",
        f"     dst: {skill_dest}",
        "",
        f"b. Add MCP server '{HERMES_MCP_SERVER}'",
        f"     hermes: {hermes_cli} mcp add {HERMES_MCP_SERVER} --command {myteam_cli} --args mcp",
        f"     env: MY_TEAM_AGENT=hermes, MY_TEAM_PORT={config_port()}",
        "",
        "c. Add shell hooks",
        f"     on_session_start:  {myteam_cli} hook session-start --agent {HERMES_HOOKS_AGENT}",
        f"     pre_llm_call:      {myteam_cli} hook prompt --agent {HERMES_HOOKS_AGENT}",
        "",
        "d. Enable autostart",
        "     my_team.config.save(autostart=True)",
        "",
        "e. Hook consent",
        f"     {HOOK_CONSENT_HINT}",
        "",
        "Context preview (session-start --agent hermes):",
        f"     {safe_ctx[:300]}{'…' if len(safe_ctx) > 300 else ''}",
    ]
    return "\n".join(lines)


def _shlex_quote(s: str) -> str:
    return _shlex.quote(s)
