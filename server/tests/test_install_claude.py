import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.anyio


def _root() -> Path:
    return Path(__file__).resolve().parents[2]


def _source() -> Path:
    market = json.loads((_root() / ".claude-plugin" / "marketplace.json").read_text(encoding="utf-8"))
    return _root() / market["plugins"][0]["source"]


def test_plugin_source_is_small_and_symlink_free():
    source = _source()
    assert (source / ".claude-plugin" / "plugin.json").is_file()
    assert (source / "skills" / "my-team" / "SKILL.md").is_file()
    files = [p for p in source.rglob("*") if p.is_file()]
    assert files and not [p for p in files if p.is_symlink()]
    assert sum(p.stat().st_size for p in files) < 1_000_000


def _claude() -> str | None:
    return shutil.which("claude")


def _run(cli: str, *args: str, env: dict) -> subprocess.CompletedProcess:
    return subprocess.run([cli, *args], env=env, stdin=subprocess.DEVNULL, capture_output=True, text=True,
                          timeout=240, check=False)


async def test_plugin_installs_from_marketplace_sandboxed(tmp_path, monkeypatch):
    cli = _claude()
    if cli is None:
        pytest.skip("claude CLI not on PATH")
    home = tmp_path / "home"
    home.mkdir()
    for key in ("HOME", "USERPROFILE", "CODEX_HOME", "OPENCODE_CONFIG_DIR", "HERMES_HOME", "LOCALAPPDATA"):
        monkeypatch.setenv(key, str(home / key.lower()))
    config = home / "claude-config"
    config.mkdir()
    env = dict(os.environ, CLAUDE_CONFIG_DIR=str(config))
    fake_cli = home / "my-team.exe"
    fake_cli.write_bytes(b"stub")
    added = _run(cli, "plugin", "marketplace", "add", str(_root()), env=env)
    assert added.returncode == 0, added.stderr
    installed = _run(cli, "plugin", "install", "my-team@my-team", "--config", f"cli_path={fake_cli}", env=env)
    assert installed.returncode == 0, installed.stderr + installed.stdout
    cached = next((config / "plugins" / "cache" / "my-team" / "my-team").glob("*"))
    assert (cached / "skills" / "my-team" / "SKILL.md").is_file()
    assert (cached / "commands" / "init.md").is_file()
    assert (cached / "hooks" / "hooks.json").is_file()
    listed = _run(cli, "plugin", "list", env=env)
    assert "my-team@my-team" in listed.stdout
    assert not list(cached.rglob("node_modules"))
