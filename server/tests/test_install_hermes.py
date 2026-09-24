"""Tests for the my-team → Hermes installer.

Tests use a temporary HERMES_HOME and stub the Hermes CLI (hermes mcp add,
hermes hooks …) so no real Hermes installation is required.
"""

from __future__ import annotations

import json
import os
import shutil
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

# Make my_team importable under the server/.venv interpreter.
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
import my_team.installers.hermes as hermes_installer
from my_team.config import load as config_load
from my_team.installers import skill_source


@pytest.fixture
def hermes_home(tmp_path: Path) -> Path:
    home = tmp_path / "hermes"
    home.mkdir()
    yield home


@pytest.fixture
def venv_myteam_cli(tmp_path: Path) -> Path:
    """A fake ``my-team`` executable that records every invocation and returns
    canned JSON for ``my-team call …`` and ``my-team mcp …``."""
    cli = tmp_path / "my-team"
    def _run() -> None:
        argv = sys.argv[1:]
        if argv[:2] == ["call", "session_register"]:
            print(json.dumps({"session_id": "sess-1", "agent_id": "agent-hermes-1"}))
        elif argv[:2] == ["call", "ticket_create"]:
            print(json.dumps({"key": "TT-42", "status": "proposed"}))
        elif argv[:2] == ["call", "ticket_claim"]:
            print(json.dumps({"key": "TT-42", "status": "in_progress", "agent_id": argv[-1]}))
        elif argv[:2] == ["call", "ticket_update"]:
            print(json.dumps({"ok": True}))
        elif argv[:2] == ["call", "events_since"]:
            print(json.dumps({"events": []}))
        elif argv[:3] == ["mcp", "add", "my-team"]:
            print(json.dumps({"ok": True}))
        else:
            print(json.dumps({"ok": True}))
        sys.exit(0)

    cli.write_text(
        f"""#!/usr/bin/env python3
import json, os, sys
sys.path.insert(0, {json.dumps(str(tmp_path))})
# The real my_team package is not available inside the fake exe; we just
# record invocations and return canned output for the ops the installer
# (`my-team install hermes`) and the plugin exercise.
if sys.argv[1:3] == ["call", "session_register"]:
    print(json.dumps({{"session_id": "sess-1", "agent_id": "agent-hermes-1"}}))
elif sys.argv[1:3] == ["call", "ticket_create"]:
    print(json.dumps({{"key": "TT-42", "status": "proposed"}}))
elif sys.argv[1:3] == ["call", "ticket_claim"]:
    print(json.dumps({{"key": "TT-42", "status": "in_progress", "agent_id": sys.argv[-1]}}))
elif sys.argv[1:3] == ["call", "ticket_update"]:
    print(json.dumps({{"ok": True}}))
elif sys.argv[1:3] == ["call", "events_since"]:
    after = json.loads(sys.argv[3])["after"] if len(sys.argv) > 3 else 0
    print(json.dumps({{"events": []}}))
elif sys.argv[1:3] == ["mcp", "add", "my-team"]:
    print(json.dumps({{"ok": True}}))
else:
    print(json.dumps({{"ok": True}}))
"""
    )
    cli.chmod(0o755)
    return cli


@pytest.fixture
def patched_env(hermes_home: Path, venv_myteam_cli: Path, tmp_path: Path) -> dict:
    """Return a copy of the environment suitable for subprocess runs: HERMES_HOME
    points at the temporary home, and my-team is on PATH via the fake venv."""
    env = os.environ.copy()
    env["HERMES_HOME"] = str(hermes_home)
    env["PATH"] = f"{venv_myteam_cli.parent}{os.pathsep}{env.get('PATH', '')}"
    src = str(Path(__file__).resolve().parents[2] / "src")
    env["PYTHONPATH"] = src + os.pathsep + env.get("PYTHONPATH", "")
    return env


@pytest.fixture
def fresh_myteam_config(tmp_path: Path) -> Path:
    """A brand-new my_team config dir (MY_TEAM_HOME) with no config.yaml yet."""
    home = tmp_path / "myteam-home"
    home.mkdir()
    return home


@pytest.fixture
def myteam_cli_exec(tmp_path: Path) -> Path:
    """The *real* my-team.exe from the venv (used only for `my-team call` in the
    bridge plugin tests, not for the installer's Hermes CLI stubs)."""
    cli = Path(__file__).resolve().parents[2] / "server" / ".venv" / "Scripts" / "my-team.exe"
    if not cli.exists():
        pytest.skip("my-team.exe not found; install the venv first")
    return cli


# ---------------------------------------------------------------------------
# Installer tests
# ---------------------------------------------------------------------------

class TestInstallHermes:
    def test_plan_lists_all_five_steps(self, hermes_home: Path, patched_env: dict) -> None:
        with patch.dict(os.environ, patched_env, clear=False):
            plan = hermes_installer._build_plan(hermes_home, "/fake/hermes", "/fake/my-team")
        plan_text = "\n".join(plan) if isinstance(plan, list) else plan
        assert "a. Install skill" in plan_text
        assert "b. Add MCP server" in plan_text
        assert "c. Add shell hooks" in plan_text
        assert "d. Enable autostart" in plan_text
        assert "e. Hook consent" in plan_text
        assert str(hermes_home) in plan_text
        assert "skills/software-development/my-team" in plan_text

    def test_install_skill_copies_tree(self, hermes_home: Path, patched_env: dict) -> None:
        with patch.dict(os.environ, patched_env, clear=False):
            src = skill_source()
            assert src.exists(), f"skill source missing: {src}"
            dst = hermes_home / "skills" / "software-development" / "my-team"
            assert not dst.exists()
            hermes_installer._install_skill(hermes_home)
            assert dst.exists()
            assert (dst / "SKILL.md").exists()
            # second call is idempotent
            hermes_installer._install_skill(hermes_home)

    def test_skill_dir_identical(self, hermes_home: Path, patched_env: dict) -> None:
        with patch.dict(os.environ, patched_env, clear=False):
            src = skill_source()
            dst = hermes_home / "skills" / "software-development" / "my-team"
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copytree(src, dst)
            assert hermes_installer._skill_dir_identical(src, dst) is True
            (dst / "extra").write_text("x")
            assert hermes_installer._skill_dir_identical(src, dst) is False

    def test_ensure_mcp_falls_back_to_config_when_no_hermes_cli(
        self, hermes_home: Path, patched_env: dict
    ) -> None:
        with (
            patch.dict(os.environ, patched_env, clear=False),
            patch.object(
                hermes_installer,
                "_hermes_cli",
                return_value="/no/such/hermes",
            ),
        ):
            hermes_installer._ensure_mcp(hermes_home, "/no/such/hermes", "/fake/my-team")
        cfg = hermes_installer._read_config(hermes_installer._hermes_home())
        assert "my-team" in cfg.get("mcp_servers", {})
        entry = cfg["mcp_servers"]["my-team"]
        assert entry["command"] == "/fake/my-team"
        assert entry["args"] == ["mcp"]
        assert "MY_TEAM_AGENT" in entry["env"]
        assert "MY_TEAM_PORT" in entry["env"]

    def test_ensure_mcp_idempotent(self, hermes_home: Path, patched_env: dict) -> None:
        with patch.dict(os.environ, patched_env, clear=False):
            cfg = hermes_installer._read_config(hermes_home)
            cfg["mcp_servers"] = {"my-team": {"command": "/x", "args": ["mcp"], "env": {}}}
            hermes_installer._write_config(hermes_home, cfg)
            with patch.object(hermes_installer, "_hermes_cli", return_value="/no/such/hermes"):
                hermes_installer._ensure_mcp(hermes_home, "/no/such/hermes", "/fake/my-team")

    def test_ensure_hooks_writes_config_yaml(self, hermes_home: Path, patched_env: dict) -> None:
        with patch.dict(os.environ, patched_env, clear=False):
            with patch.object(hermes_installer, "_hermes_cli", return_value="/no/such/hermes"):
                hermes_installer._ensure_hooks(hermes_home, "/no/such/hermes", "/fake/my-team")
            cfg = hermes_installer._read_config(hermes_home)
            hooks = cfg.get("hooks", {})
            starts = hooks.get("on_session_start", [])
            prompts = hooks.get("pre_llm_call", [])
            assert any("hook session-start --agent hermes" in (e.get("command") or "") for e in starts)
            assert any("hook prompt --agent hermes" in (e.get("command") or "") for e in prompts)
            assert any(e.get("command") == "/fake/my-team" for e in starts)
            assert any(e.get("command") == "/fake/my-team" for e in prompts)

    def test_ensure_hooks_idempotent(self, hermes_home: Path, patched_env: dict) -> None:
        with patch.dict(os.environ, patched_env, clear=False):
            cfg = hermes_installer._read_config(hermes_home)
            cfg["hooks"] = {
                "on_session_start": [
                    {"command": "/fake/my-team", "args": ["hook", "session-start", "--agent", "hermes"]}
                ],
                "pre_llm_call": [
                    {"command": "/fake/my-team", "args": ["hook", "prompt", "--agent", "hermes"]}
                ],
            }
            hermes_installer._write_config(hermes_installer._hermes_home(), cfg)
            with patch.object(hermes_installer, "_hermes_cli", return_value="/no/such/hermes"):
                hermes_installer._ensure_hooks(hermes_home, "/no/such/hermes", "/fake/my-team")

    def test_ensure_autostart(self, fresh_myteam_config: Path) -> None:
        with patch.object(hermes_installer, "_hermes_home", return_value=fresh_myteam_config):
            # The autostart path calls my_team.config.save which writes to MY_TEAM_HOME.
            # Point MY_TEAM_HOME at a temp dir so we don't touch the real one.
            with patch.dict(os.environ, {"MY_TEAM_HOME": str(fresh_myteam_config)}):
                hermes_installer._ensure_autostart()
            cfg = config_load()
            assert cfg.get("autostart") is True

    def test_install_end_to_end_skips_confirm_when_yes(
        self, hermes_home: Path, patched_env: dict
    ) -> None:
        with patch.dict(os.environ, patched_env, clear=False), patch.object(
            hermes_installer, "_hermes_cli", return_value="/no/such/hermes"
        ):
            ret = hermes_installer.install(yes=True)
        assert ret == 0
        cfg = hermes_installer._read_config(hermes_home)
        assert "my-team" in cfg.get("mcp_servers", {})
        hooks = cfg.get("hooks", {})
        assert any(
            "hook session-start --agent hermes" in (e.get("command") or "")
            for e in hooks.get("on_session_start", [])
        )
        assert any(
            "hook prompt --agent hermes" in (e.get("command") or "")
            for e in hooks.get("pre_llm_call", [])
        )

    def test_install_returns_1_when_user_declines(
        self, hermes_home: Path, patched_env: dict, capsys
    ) -> None:
        with (
            patch.dict(os.environ, patched_env, clear=False),
            patch("my_team.installers.confirm", return_value=False),
        ):
            ret = hermes_installer.install(yes=False)
        assert ret == 1
