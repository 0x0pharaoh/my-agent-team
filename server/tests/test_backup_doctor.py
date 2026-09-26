import pytest

from my_team import doctor
from my_team.db import backup

pytestmark = pytest.mark.anyio


async def test_daily_backup_is_idempotent_forceable_and_pruned(agent, home):
    written = backup.backup_all()
    assert sorted(p.name.split("-")[0] for p in written)[-1] == "registry" and len(written) == 2
    assert backup.backup_all() == []
    assert len(backup.backup_all(force=True)) == 2
    folder = home / "data" / "backups"
    for day in range(1, 10):
        (folder / f"registry-2020-01-{day:02}.db").write_bytes(b"")
    backup.backup_all(force=True)
    assert len(list(folder.glob("registry-????-??-??.db"))) == backup.KEEP


async def test_doctor_passes_a_healthy_install_and_fails_a_corrupt_db(agent, home, capsys):
    doctor.run()
    report = capsys.readouterr().out
    assert "fail" not in report and "integrity ok" in report and "is private" in report
    (home / "data" / "projects" / "broken.db").write_bytes(b"not a database" * 100)
    assert doctor.run() == 1
    assert "fail  broken.db" in capsys.readouterr().out


def _adapter_env(monkeypatch, tmp_path):
    home = tmp_path / "home"
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex"))
    monkeypatch.setenv("OPENCODE_CONFIG_DIR", str(tmp_path / "oc"))
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "hermes"))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "la"))
    return home


def _write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


async def test_doctor_adapter_checks_pass_when_installed(agent, home, tmp_path, monkeypatch, capsys):
    base = _adapter_env(monkeypatch, tmp_path)
    _write(base / ".claude" / "settings.json", '{"enabledPlugins": {"my-team@my-team": true}}')
    _write(base / ".agents" / "skills" / "my-team" / "SKILL.md", "# my-team\n")
    _write(tmp_path / "codex" / "config.toml", '[mcp_servers.my-team]\ncommand = "my-team"\n')
    _write(tmp_path / "oc" / "opencode.jsonc",
            '// project config\n{"mcp": {"servers": {"my-team": {"type": "local",}}},}\n')
    _write(tmp_path / "oc" / "plugins" / "my-team.js", "export default {};\n")
    _write(tmp_path / "hermes" / "config.yaml", 'hooks:\n  pre_llm_call:\n    - command: "x hook prompt --agent hermes"\n')
    assert doctor.run() == 0
    report = capsys.readouterr().out
    assert "Claude Code plugin my-team enabled" in report
    assert "SKILL.md present" in report
    assert "Codex MCP server my-team registered" in report
    assert "OpenCode plugin + MCP entry present" in report
    assert "Hermes hooks reference my-team" in report


async def test_doctor_adapter_checks_warn_only_when_missing(agent, home, tmp_path, monkeypatch, capsys):
    _adapter_env(monkeypatch, tmp_path)
    assert doctor.run() == 0
    report = capsys.readouterr().out
    assert "fail" not in report
    assert "settings not found" in report and "skill missing" in report and "config not found" in report
    _write(tmp_path / "home" / ".claude" / "settings.json", '{"enabledPlugins": {}}')
    _write(tmp_path / "hermes" / "config.yaml", "mcp_servers: {}\n")
    assert doctor.run() == 0
    report = capsys.readouterr().out
    assert "not enabled" in report and "lacks my-team hooks" in report
