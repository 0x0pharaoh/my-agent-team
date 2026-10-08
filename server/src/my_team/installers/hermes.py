"""my-team install for Hermes — idempotent installation into a Hermes profile."""

from __future__ import annotations

import itertools
import json
import os
import re
import shutil
import subprocess
from pathlib import Path

from my_team.config import load as config_load
from my_team.config import save as config_save
from my_team.installers import adapter, confirm, copy_skill
from my_team.installers import cli_path as my_team_cli_path

HERMES_MCP_SERVER = "my-team"
HERMES_HOOKS_AGENT = "hermes"
PLUGIN_DIR_NAME = "my-team-kanban"
PLUGIN_SRC = adapter("hermes", "plugin")


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


def _plugin_dest(home: Path) -> Path:
    return home / "plugins" / PLUGIN_DIR_NAME


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
    raise FileNotFoundError("hermes CLI not found. Install Hermes or set HERMES_HOME.")


def _install_skill(home: Path) -> None:
    dst = _skill_dest(home)
    if copy_skill(dst):
        print(f"installed skill: {dst}")
    else:
        print(f"skill already installed: {dst}")


def _install_plugin(home: Path, myteam_cli: str) -> None:
    dst = _plugin_dest(home)
    if dst.is_dir() and (dst / "__init__.py").is_file():
        print(f"plugin already installed: {dst}")
    else:
        dst.parent.mkdir(parents=True, exist_ok=True)
        if dst.exists():
            shutil.rmtree(dst)
        shutil.copytree(PLUGIN_SRC, dst, ignore=shutil.ignore_patterns("__pycache__"))
        init_py = dst / "__init__.py"
        content = init_py.read_text(encoding="utf-8")
        old = 'return os.environ.get("HERMES_MY_TEAM_CLI") or shutil.which("my-team") or "my-team"'
        new = f'return os.environ.get("HERMES_MY_TEAM_CLI") or {json.dumps(myteam_cli)}'
        init_py.write_text(content.replace(old, new), encoding="utf-8")
        print(f"installed plugin: {dst}")
    _ensure_plugins_enabled(home)


def _plugins_enabled(text: str) -> bool:
    if "plugins:" not in text or "enabled:" not in text:
        return False
    return bool(re.search(r"(?m)^\s*-\s*my-team-kanban\s*$", text))


def _ensure_plugins_enabled(home: Path) -> None:
    cfg_path = config_path(home)
    cfg_path.parent.mkdir(parents=True, exist_ok=True)
    text = cfg_path.read_text(encoding="utf-8") if cfg_path.exists() else ""
    if _plugins_enabled(text):
        print("bridge plugin already enabled via plugins.enabled")
        return
    if not text.strip():
        cfg_path.write_text(
            "plugins:\n  enabled:\n    - my-team-kanban\n", encoding="utf-8"
        )
    elif re.search(r"(?m)^plugins\s*:", text) is None:
        with open(cfg_path, "a", encoding="utf-8") as f:
            f.write("\nplugins:\n  enabled:\n    - my-team-kanban\n")
    else:
        lines = text.splitlines(keepends=True)
        out: list[str] = []
        in_plugins = False
        plugins_indent = 0
        enabled_done = False
        for i, line in enumerate(lines):
            stripped = line.lstrip()
            indent = len(line) - len(stripped)
            if re.match(r"plugins\s*:", stripped):
                in_plugins = True
                plugins_indent = indent
                out.append(line)
                continue
            if (
                in_plugins
                and re.match(r"enabled\s*:", stripped)
                and indent > plugins_indent
            ):
                out.append(line)
                if not any(
                    re.match(r"\s*-\s*my-team-kanban\s*$", l)
                    for l in lines[i + 1 : i + 20]
                ):
                    out.append(" " * (indent + 2) + "- my-team-kanban\n")
                enabled_done = True
                in_plugins = False
                continue
            if in_plugins and stripped and indent <= plugins_indent:
                if not enabled_done:
                    out.append(" " * (plugins_indent + 2) + "enabled:\n")
                    out.append(" " * (plugins_indent + 4) + "- my-team-kanban\n")
                    enabled_done = True
                in_plugins = False
            out.append(line)
        if in_plugins and not enabled_done:
            out.append(" " * (plugins_indent + 2) + "enabled:\n")
            out.append(" " * (plugins_indent + 4) + "- my-team-kanban\n")
        backup = str(cfg_path) + ".my-team.bak"
        shutil.copy2(cfg_path, backup)
        cfg_path.write_text("".join(out), encoding="utf-8")
    print("bridge plugin enabled via plugins.enabled")


def _hook_entries(myteam_cli: str) -> list[tuple[str, str | None, str]]:
    base = f'"{myteam_cli}"'
    return [
        ("pre_llm_call", None, f"{base} hook prompt --agent hermes"),
        ("pre_tool_call", "mcp__my_team__.*", f"{base} hook tool --agent hermes"),
        ("pre_tool_call", "write_file|patch", f"{base} hook pre-edit --agent hermes"),
    ]


def _hooks_registered(text: str, myteam_cli: str) -> bool:
    return all(json.dumps(cmd) in text for _, _, cmd in _hook_entries(myteam_cli))


def _hooks_text_block(myteam_cli: str) -> str:
    """Hook commands as JSON strings: Hermes splits with shlex posix=False, so one quote layer survives."""
    lines = ["hooks:"]
    for event, group in itertools.groupby(
        _hook_entries(myteam_cli), key=lambda entry: entry[0]
    ):
        lines.append(f"  {event}:")
        for _, matcher, cmd in group:
            if matcher is None:
                lines.append(f"    - command: {json.dumps(cmd)}")
            else:
                lines.append(f'    - matcher: "{matcher}"')
                lines.append(f"      command: {json.dumps(cmd)}")
    return "\n".join(lines) + "\n"


def mcp_registered(home: Path) -> bool:
    """True when config.yaml already lists my-team under mcp_servers (checked per block, not substring)."""
    try:
        text = config_path(home).read_text(encoding="utf-8")
    except OSError:
        return False
    section = re.search(r"(?m)^mcp_servers\s*:", text)
    if not section:
        return False
    rest = text[section.end() :]
    dedented = re.search(r"(?m)^\S", rest)
    return bool(
        re.search(
            r"(?m)^\s+my-team\s*:", rest[: dedented.start()] if dedented else rest
        )
    )


def _ensure_mcp(home: Path, hermes_cli: str, myteam_cli: str) -> None:
    """Register the MCP server via `hermes mcp add`, attached to the console for its prompts."""
    if mcp_registered(home):
        print("MCP server my-team already registered")
        return
    cmd = [
        hermes_cli,
        "mcp",
        "add",
        HERMES_MCP_SERVER,
        "--command",
        myteam_cli,
        "--env",
        "MY_TEAM_AGENT_TYPE=hermes",
        "--args",
        "mcp",
    ]
    print(f"running: {' '.join(cmd)}")
    try:
        proc = subprocess.run(cmd, check=False)
    except (FileNotFoundError, OSError) as exc:
        print(f"hermes CLI not found: {exc}")
        return
    if mcp_registered(home):
        print("MCP server my-team registered via hermes CLI")
    else:
        print(
            f"hermes mcp add did not register my-team (rc={proc.returncode}); add it manually"
        )


def _ensure_hooks(home: Path, hermes_cli: str, myteam_cli: str) -> None:
    """Append shell hooks to config.yaml directly; `hermes hooks` has list/test/doctor but no `add`."""
    cfg_path = config_path(home)
    _merge_hooks_text(cfg_path, myteam_cli)


def _merge_hooks_text(cfg_path: Path, myteam_cli: str) -> None:
    cfg_path.parent.mkdir(parents=True, exist_ok=True)
    text = cfg_path.read_text(encoding="utf-8") if cfg_path.exists() else ""
    if _hooks_registered(text, myteam_cli):
        print("Hermes hooks for my-team already registered")
        return
    if re.search(r"^hooks\s*:", text, re.MULTILINE):
        snippet = _hooks_text_block(myteam_cli).replace("hooks:\n", "", 1)
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
        f"install kanban bridge plugin to {_plugin_dest(home)}",
        f"register MCP server ({hermes_cli} mcp add {HERMES_MCP_SERVER} ...)",
        "append pre_llm_call + pre_tool_call hooks to config.yaml",
        "enable bridge plugin via plugins.enabled",
        "enable autostart in my-team config",
    ]


def install(yes: bool) -> int:
    home = hermes_home()
    hermes_cli = _hermes_cli()
    myteam_cli = my_team_cli_path()
    if not confirm(_build_plan(home, hermes_cli, myteam_cli), yes):
        return 1
    _install_skill(home)
    _install_plugin(home, myteam_cli)
    _ensure_mcp(home, hermes_cli, myteam_cli)
    _ensure_hooks(home, hermes_cli, myteam_cli)
    _ensure_autostart()
    print(
        "\nNext steps: approve the hooks on the first terminal `hermes` run (or set hooks_auto_accept), "
        "check `hermes hooks list`, then restart Hermes."
    )
    return 0
