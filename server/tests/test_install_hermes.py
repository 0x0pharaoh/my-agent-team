"""Tests for the my-team -> Hermes installer. Everything runs in tmp dirs; the real config is never touched."""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

import my_team.installers.hermes as hermes_installer


def _fake_hermes_cli(tmp_path: Path) -> Path:
    cli = tmp_path / "hermes"
    cli.write_text("#!/usr/bin/env python3\nimport sys, json\nprint(json.dumps({\"ok\": True}))\n")
    return cli


def _write_config(home: Path, text: str) -> None:
    home.mkdir(parents=True, exist_ok=True)
    (home / "config.yaml").write_text(text, encoding="utf-8")


def _read_config_text(home: Path) -> str:
    return (home / "config.yaml").read_text(encoding="utf-8")


@pytest.fixture
def venv_myteam_cli(tmp_path: Path) -> Path:
    cli = tmp_path / "my-team"
    cli.write_text(
        """#!/usr/bin/env python3
import json, sys
if len(sys.argv) > 2 and sys.argv[1] == "call":
    print(json.dumps({"session_id": "sess-1", "agent_id": "agent-hermes-1"}))
elif len(sys.argv) > 3 and sys.argv[1:3] == ["mcp", "add"]:
    print(json.dumps({"ok": True}))
else:
    print(json.dumps({"ok": True}))
"""
    )
    return cli


@pytest.fixture
def patched_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict:
    """HERMES_HOME and LOCALAPPDATA point at tmp dirs; real agent CLIs can never run."""
    env = dict(os.environ)
    env["HERMES_HOME"] = str(tmp_path / "hermes-home")
    env["LOCALAPPDATA"] = str(tmp_path / "la")
    env["PATH"] = os.pathsep.join([str(tmp_path), env.get("PATH", "")])
    monkeypatch.setenv("HERMES_HOME", env["HERMES_HOME"])
    monkeypatch.setenv("LOCALAPPDATA", env["LOCALAPPDATA"])
    monkeypatch.setattr("shutil.which", lambda name, *a, **k: None)
    return env


class TestInstallHermes:
    def test_install_returns_1_when_user_declines(self, tmp_path: Path) -> None:
        home = tmp_path / "hermes-home"
        home.mkdir(parents=True, exist_ok=True)
        with (
            patch.object(hermes_installer, "_hermes_cli", return_value="/no/such/hermes"),
            patch.object(hermes_installer, "_hermes_home", return_value=home),
            patch("my_team.installers.hermes.confirm", return_value=False),
        ):
            ret = hermes_installer.install(yes=False)
            assert ret == 1

    def test_install_skill_copies_tree(self, tmp_path: Path) -> None:
        home = tmp_path / "hermes-home"
        with patch.object(hermes_installer, "_hermes_home", return_value=home):
            hermes_installer._install_skill(home)
            dst = home / "skills" / "software-development" / "my-team"
            assert dst.is_dir()
            assert (dst / "SKILL.md").is_file()

    def test_install_skill_idempotent(self, tmp_path: Path) -> None:
        home = tmp_path / "hermes-home"
        skill_dest = home / "skills" / "software-development" / "my-team"
        skill_dest.mkdir(parents=True, exist_ok=True)
        (skill_dest / "SKILL.md").write_text("existing\n")
        with patch.object(hermes_installer, "_hermes_home", return_value=home):
            hermes_installer._install_skill(home)
            assert skill_dest.is_dir()

    def test_ensure_mcp_registers_via_cli(
        self, tmp_path: Path, patched_env: dict, venv_myteam_cli: Path
    ) -> None:
        home = tmp_path / "hermes-home"
        hermes_cli = _fake_hermes_cli(tmp_path)
        with (
            patch.dict(os.environ, patched_env, clear=False),
            patch.object(hermes_installer, "_hermes_cli", return_value=str(hermes_cli)),
            patch("subprocess.run") as mock_run,
        ):
            mock_run.return_value = subprocess.CompletedProcess(
                args=[], returncode=0, stdout="ok", stderr=""
            )
            hermes_installer._ensure_mcp(home, str(hermes_cli), str(venv_myteam_cli))
            assert mock_run.called

    def test_ensure_mcp_idempotent(
        self, tmp_path: Path, patched_env: dict, venv_myteam_cli: Path
    ) -> None:
        home = tmp_path / "hermes-home"
        hermes_cli = _fake_hermes_cli(tmp_path)
        with (
            patch.dict(os.environ, patched_env, clear=False),
            patch.object(hermes_installer, "_hermes_cli", return_value=str(hermes_cli)),
            patch("subprocess.run") as mock_run,
        ):
            mock_run.return_value = subprocess.CompletedProcess(
                args=[], returncode=0, stdout="", stderr=""
            )
            hermes_installer._ensure_mcp(home, str(hermes_cli), str(venv_myteam_cli))
            hermes_installer._ensure_mcp(home, str(hermes_cli), str(venv_myteam_cli))
            assert mock_run.call_count == 2

    def test_ensure_mcp_no_cli_prints_error_and_skips(self, tmp_path: Path) -> None:
        home = tmp_path / "hermes-home"
        with (
            patch.object(hermes_installer, "_hermes_cli", return_value="/no/such/hermes"),
            patch("subprocess.run", side_effect=FileNotFoundError("not found")),
        ):
            hermes_installer._ensure_mcp(home, "/no/such/hermes", "/fake/my-team")

    def test_ensure_hooks_writes_config_yaml(
        self, tmp_path: Path, patched_env: dict, venv_myteam_cli: Path
    ) -> None:
        """Hook commands round-trip: command: {json.dumps(cmd)} parses back to the exact command."""
        home = tmp_path / "hermes-home"
        cli = str(venv_myteam_cli)
        cmd_session = f'"{cli}" hook session-start --agent hermes'
        cmd_prompt = f'"{cli}" hook prompt --agent hermes'
        with (
            patch.dict(os.environ, patched_env, clear=False),
            patch.object(hermes_installer, "_hermes_cli", return_value="/no/such/hermes"),
        ):
            hermes_installer._ensure_hooks(home, "/no/such/hermes", cli)
        text = _read_config_text(home)
        assert "--agent hermes" in text
        for line in text.splitlines():
            if "command:" in line:
                val = line.split("command:", 1)[1].strip()
                assert json.loads(val) in (cmd_session, cmd_prompt)

    def test_ensure_hooks_leaves_existing_hooks_block_alone(
        self, tmp_path: Path, patched_env: dict, venv_myteam_cli: Path
    ) -> None:
        home = tmp_path / "hermes-home"
        before = "mcp_servers: {}\nhooks:\n  pre_llm_call: []\n"
        _write_config(home, before)
        with (
            patch.dict(os.environ, patched_env, clear=False),
            patch.object(hermes_installer, "_hermes_cli", return_value="/no/such/hermes"),
        ):
            hermes_installer._ensure_hooks(home, "/no/such/hermes", str(venv_myteam_cli))
        assert _read_config_text(home) == before

    def test_real_localappdata_hermes_config_mtime_unchanged(self, tmp_path: Path) -> None:
        """The real %LOCALAPPDATA%/hermes/config.yaml mtime is unchanged after install."""
        real_home = Path(os.environ.get("LOCALAPPDATA", "")) / "hermes"
        cfg_path = real_home / "config.yaml"
        if not cfg_path.exists():
            pytest.skip("real config.yaml does not exist")
        before = cfg_path.stat().st_mtime
        home = tmp_path / "fake-home"
        home.mkdir(parents=True, exist_ok=True)
        with (
            patch.object(hermes_installer, "_hermes_home", return_value=home),
            patch.object(hermes_installer, "_hermes_cli", return_value="/no/such/hermes"),
            patch.dict(os.environ, {"HERMES_HOME": str(home), "LOCALAPPDATA": str(tmp_path)}, clear=False),
        ):
            ret = hermes_installer.install(yes=True)
        assert ret == 0
        assert cfg_path.stat().st_mtime == before

    def test_ensure_hooks_idempotent(
        self, tmp_path: Path, patched_env: dict, venv_myteam_cli: Path
    ) -> None:
        home = tmp_path / "hermes-home"
        _write_config(home, "hooks:\n  on_session_start:\n    - command: /old/my-team hook session-start --agent hermes\n")
        with (
            patch.dict(os.environ, patched_env, clear=False),
            patch.object(hermes_installer, "_hermes_cli", return_value=str(venv_myteam_cli)),
        ):
            hermes_installer._ensure_hooks(home, str(venv_myteam_cli), str(venv_myteam_cli))
        text = _read_config_text(home)
        assert text.count("hook session-start --agent hermes") == 1

    def test_ensure_autostart(self, tmp_path: Path) -> None:
        home = tmp_path / "hermes-home"
        with (
            patch.object(hermes_installer, "_hermes_home", return_value=home),
            patch("my_team.installers.hermes.config_load", return_value={}),
            patch("my_team.installers.hermes.config_save") as save,
        ):
            hermes_installer._ensure_autostart()
            save.assert_called_once()

    def test_install_end_to_end_no_prompt_when_yes(
        self, tmp_path: Path, patched_env: dict, venv_myteam_cli: Path
    ) -> None:
        home = tmp_path / "hermes-home"
        hermes_cli = _fake_hermes_cli(tmp_path)
        cli = str(venv_myteam_cli)
        with (
            patch.dict(os.environ, patched_env, clear=False),
            patch.object(hermes_installer, "_hermes_cli", return_value=str(hermes_cli)),
            patch.object(hermes_installer, "_hermes_home", return_value=home),
            patch.object(hermes_installer, "my_team_cli_path", return_value=cli),
            patch("subprocess.run") as mock_run,
            patch("my_team.installers.hermes.confirm") as confirm_mock,
        ):
            mock_run.return_value = subprocess.CompletedProcess(
                args=[], returncode=0, stdout="ok", stderr=""
            )
            confirm_mock.return_value = True
            ret = hermes_installer.install(yes=True)
        assert ret == 0
        confirm_mock.assert_called_once()
        assert confirm_mock.call_args[0][1] is True
        text = _read_config_text(home)
        assert "--agent hermes" in text
        plugin_init = (home / "plugins" / "my-team" / "__init__.py").read_text(encoding="utf-8")
        assert json.dumps(cli) in plugin_init
        assert 'shutil.which("my-team")' not in plugin_init
        assert (home / "plugins" / "my-team" / "plugin.yaml").is_file()
        assert (home / "plugins" / "my-team" / "bridge.py").is_file()

    def test_install_end_to_end_falls_back_when_hermes_cli_missing(
        self, tmp_path: Path, patched_env: dict, venv_myteam_cli: Path
    ) -> None:
        home = tmp_path / "hermes-home"
        with (
            patch.dict(os.environ, patched_env, clear=False),
            patch.object(hermes_installer, "_hermes_cli", return_value="/no/such/hermes"),
            patch.object(hermes_installer, "_hermes_home", return_value=home),
            patch("subprocess.run", side_effect=FileNotFoundError("not found")),
        ):
            ret = hermes_installer.install(yes=True)
        assert ret == 0
        text = _read_config_text(home)
        assert "--agent hermes" in text
