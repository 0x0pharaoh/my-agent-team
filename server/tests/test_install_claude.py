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


def test_plugin_version_matches_package():
    from my_team import __version__
    manifest = json.loads((_source() / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))
    assert manifest["version"] == __version__


def test_plugin_source_is_small_and_symlink_free():
    source = _source()
    assert (source / ".claude-plugin" / "plugin.json").is_file()
    assert (source / "skills" / "my-team" / "SKILL.md").is_file()
    files = [p for p in source.rglob("*") if p.is_file()]
    assert files and not [p for p in files if p.is_symlink()]
    assert sum(p.stat().st_size for p in files) < 1_000_000


def test_pre_tool_use_covers_notebook_edits():
    entries = json.loads((_source() / "hooks" / "hooks.json").read_text(encoding="utf-8"))["hooks"]["PreToolUse"]
    notebook = next(entry["hooks"][0] for entry in entries if entry.get("matcher") == "NotebookEdit")
    assert notebook["tool"] == "hook_pre_edit"
    assert notebook["input"]["file_path"] == "${tool_input.notebook_path}"


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


def test_install_stops_at_first_failure(monkeypatch, capsys):
    from my_team.installers import claude_code
    monkeypatch.setattr(claude_code, "cli_path", lambda: "/fake/my-team.exe")
    calls = []
    real_run = subprocess.run

    def fake_run(step, **kwargs):
        if step[0] != "claude":
            return real_run(step, **kwargs)
        calls.append(step)
        return subprocess.CompletedProcess(step, 1 if len(calls) == 1 else 0)

    monkeypatch.setattr(claude_code.subprocess, "run", fake_run)
    assert claude_code.install(yes=True) == 1
    assert len(calls) == 1
    assert "rc=1" in capsys.readouterr().out


def test_install_prints_done_on_success(monkeypatch, capsys):
    from my_team.installers import claude_code
    monkeypatch.setattr(claude_code, "cli_path", lambda: "/fake/my-team.exe")
    calls = []
    real_run = subprocess.run

    def fake_run(step, **kwargs):
        if step[0] != "claude":
            return real_run(step, **kwargs)
        calls.append(step)
        return subprocess.CompletedProcess(step, 0)

    monkeypatch.setattr(claude_code.subprocess, "run", fake_run)
    assert claude_code.install(yes=True) == 0
    assert len(calls) == 2
    assert "Done." in capsys.readouterr().out


def test_wheel_fallback_pins_release_tag(monkeypatch):
    from my_team import __version__
    from my_team.installers import claude_code
    monkeypatch.setattr(claude_code, "checkout", lambda: None)
    monkeypatch.setattr(claude_code, "cli_path", lambda: "/fake/my-team.exe")
    seen = {}
    real_run = subprocess.run

    def fake_run(step, **kwargs):
        if step[0] != "claude":
            return real_run(step, **kwargs)
        return subprocess.CompletedProcess(step, 0)

    monkeypatch.setattr(claude_code, "confirm", lambda plan, yes: seen.setdefault("plan", plan) or True)
    monkeypatch.setattr(claude_code.subprocess, "run", fake_run)
    assert claude_code.install(yes=True) == 0
    assert f"https://github.com/0x0pharaoh/my-agent-team.git#v{__version__}" in seen["plan"][1]
