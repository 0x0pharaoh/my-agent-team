import json
import os
import shutil
import subprocess
from pathlib import Path

from my_team import config
from my_team.installers import cli_path, confirm, copy_skill, skill_source

PLACEHOLDER = '"__MY_TEAM_CLI__"'


def config_dir(home: Path) -> Path:
    if explicit := os.environ.get("OPENCODE_CONFIG_DIR"):
        return Path(explicit)
    if xdg := os.environ.get("XDG_CONFIG_HOME"):
        return Path(xdg) / "opencode"
    return home / ".config" / "opencode"


def install(yes: bool) -> int:
    oc = shutil.which("opencode2") or shutil.which("opencode")
    if oc is None:
        print("OpenCode V2 not found on PATH")
        return 1
    home = Path.home()
    target = config_dir(home)
    skill = home / ".agents" / "skills" / "my-team"
    cli = str(Path(cli_path()).resolve())
    plan = [
        f"copy my-team skill to {skill}",
        "add or refresh the OpenCode MCP server my-team",
        f"write OpenCode plugin to {target / 'plugins' / 'my-team.js'}",
        "let the my-team daemon start on demand (no OS service)",
    ]
    if not confirm(plan, yes):
        return 1
    copy_skill(skill)
    added = subprocess.run([oc, "mcp", "add", "my-team", "--global", "--", cli, "mcp"], check=False)
    if added.returncode:
        return 1
    _write_plugin(target, cli)
    config.save(autostart=True)
    print("\nDone. Run `opencode reload` (or restart OpenCode), then run /my-team:init in a project.")
    return 0


def _write_plugin(target: Path, cli: str) -> None:
    source = skill_source().parents[1] / "adapters" / "opencode" / "my-team.js"
    content = source.read_text(encoding="utf-8").replace(PLACEHOLDER, json.dumps(cli))
    path = target / "plugins" / "my-team.js"
    if path.is_file() and path.read_text(encoding="utf-8") == content:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
