import json
import os
import sys
from pathlib import Path

from my_team.installers import opencode


def test_install_is_idempotent_and_touches_only_temp_dirs(tmp_path, monkeypatch):
    real_dir = Path.home() / ".config" / "opencode"
    real_jsonc = real_dir / "opencode.jsonc"
    before_jsonc = real_jsonc.read_bytes() if real_jsonc.is_file() else None
    before_plugin = (real_dir / "plugins" / "my-team.js").is_file()
    home = tmp_path / "home"
    decoy = tmp_path / "decoy"
    cfg = tmp_path / "oc-config"
    my_team_home = tmp_path / "my-team"
    skill = tmp_path / "skill"
    skill.mkdir()
    (skill / "SKILL.md").write_text("my-team skill\n", encoding="utf-8")
    calls = tmp_path / "calls.jsonl"
    _stub_opencode(tmp_path, monkeypatch, calls)
    cli = str(tmp_path / "bin" / "my-team.exe")

    monkeypatch.setenv("HOME", str(decoy))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setenv("OPENCODE_CONFIG_DIR", str(cfg))
    monkeypatch.setenv("MY_TEAM_HOME", str(my_team_home))
    monkeypatch.setattr("my_team.installers.skill_source", lambda: skill)
    monkeypatch.setattr(opencode, "cli_path", lambda: cli)
    _which(monkeypatch, {"opencode2": _resolved(tmp_path / "bin", "opencode2")})

    assert opencode.install(yes=True) == 0
    expect = home if os.name == "nt" else decoy
    other = decoy if os.name == "nt" else home
    plugin = cfg / "plugins" / "my-team.js"
    first_plugin = plugin.read_text(encoding="utf-8")
    first_skill = _tree(expect / ".agents" / "skills" / "my-team")
    assert opencode.install(yes=True) == 0

    assert plugin.read_text(encoding="utf-8") == first_plugin
    assert _tree(expect / ".agents" / "skills" / "my-team") == first_skill
    assert "__MY_TEAM_CLI__" not in first_plugin
    assert json.dumps(cli) in first_plugin
    assert [json.loads(line) for line in calls.read_text(encoding="utf-8").splitlines()] == [
        ["mcp", "add", "my-team", "--global", "--", cli, "mcp"],
        ["mcp", "add", "my-team", "--global", "--", cli, "mcp"],
    ]
    assert (my_team_home / "config" / "config.toml").read_text(encoding="utf-8").splitlines()[1] == "autostart = true"
    assert not (other / ".agents").exists()
    after_jsonc = real_jsonc.read_bytes() if real_jsonc.is_file() else None
    assert after_jsonc == before_jsonc
    assert (real_dir / "plugins" / "my-team.js").is_file() == before_plugin


def test_install_prefers_opencode2(tmp_path, monkeypatch):
    calls2 = tmp_path / "calls2.jsonl"
    calls1 = tmp_path / "calls1.jsonl"
    _stub_opencode(tmp_path, monkeypatch, calls2, name="opencode2")
    _stub_opencode(tmp_path, monkeypatch, calls1, name="opencode")
    _temp_env(tmp_path, monkeypatch)
    bindir = tmp_path / "bin"
    cli = str(bindir / "my-team.exe")
    _which(monkeypatch, {"opencode2": _resolved(bindir, "opencode2"), "opencode": _resolved(bindir, "opencode")})
    assert opencode.install(yes=True) == 0
    assert [json.loads(line) for line in calls2.read_text(encoding="utf-8").splitlines()] == [
        ["mcp", "add", "my-team", "--global", "--", cli, "mcp"],
    ]
    assert not calls1.exists()


def test_install_falls_back_to_opencode(tmp_path, monkeypatch):
    calls = tmp_path / "calls.jsonl"
    _stub_opencode(tmp_path, monkeypatch, calls, name="opencode")
    _temp_env(tmp_path, monkeypatch)
    bindir = tmp_path / "bin"
    cli = str(bindir / "my-team.exe")
    _which(monkeypatch, {"opencode": _resolved(bindir, "opencode")})
    assert opencode.install(yes=True) == 0
    assert [json.loads(line) for line in calls.read_text(encoding="utf-8").splitlines()] == [
        ["mcp", "add", "my-team", "--global", "--", cli, "mcp"],
    ]


def test_install_returns_1_without_opencode_v2(tmp_path, monkeypatch, capsys):
    home = tmp_path / "home"
    cfg = tmp_path / "oc-config"
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setenv("OPENCODE_CONFIG_DIR", str(cfg))
    _which(monkeypatch, {})

    def _no_confirm(plan, yes):
        raise AssertionError("confirm must not run without the V2 CLI")

    monkeypatch.setattr(opencode, "confirm", _no_confirm)
    assert opencode.install(yes=True) == 1
    assert "OpenCode V2 not found on PATH" in capsys.readouterr().out
    assert not (cfg / "plugins" / "my-team.js").exists()
    assert not (home / ".agents").exists()


def test_install_returns_1_when_mcp_add_fails(tmp_path, monkeypatch):
    _stub_opencode(tmp_path, monkeypatch, tmp_path / "calls.jsonl", exit_code=1)
    _temp_env(tmp_path, monkeypatch)
    _which(monkeypatch, {"opencode2": _resolved(tmp_path / "bin", "opencode2")})
    assert opencode.install(yes=True) == 1


def test_install_returns_1_when_user_declines(tmp_path, monkeypatch):
    home = tmp_path / "home"
    cfg = tmp_path / "oc-config"
    _stub_opencode(tmp_path, monkeypatch, tmp_path / "calls.jsonl")
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setenv("OPENCODE_CONFIG_DIR", str(cfg))
    monkeypatch.setattr(opencode, "confirm", lambda plan, yes: False)
    _which(monkeypatch, {"opencode2": _resolved(tmp_path / "bin", "opencode2")})
    assert opencode.install(yes=False) == 1
    assert not (cfg / "plugins" / "my-team.js").exists()
    assert not (home / ".agents").exists()


def test_config_dir_prefers_env_then_xdg(tmp_path, monkeypatch):
    home = tmp_path / "home"
    monkeypatch.delenv("OPENCODE_CONFIG_DIR", raising=False)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    assert opencode.config_dir(home) == tmp_path / "xdg" / "opencode"
    monkeypatch.delenv("XDG_CONFIG_HOME")
    assert opencode.config_dir(home) == home / ".config" / "opencode"
    monkeypatch.setenv("OPENCODE_CONFIG_DIR", str(tmp_path / "oc"))
    assert opencode.config_dir(home) == tmp_path / "oc"


def _temp_env(tmp_path, monkeypatch):
    home = tmp_path / "home"
    skill = tmp_path / "skill"
    skill.mkdir(exist_ok=True)
    (skill / "SKILL.md").write_text("my-team skill\n", encoding="utf-8")
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setenv("OPENCODE_CONFIG_DIR", str(tmp_path / "oc-config"))
    monkeypatch.setenv("MY_TEAM_HOME", str(tmp_path / "my-team"))
    monkeypatch.setattr("my_team.installers.skill_source", lambda: skill)
    monkeypatch.setattr(opencode, "cli_path", lambda: str(tmp_path / "bin" / "my-team.exe"))


def _which(monkeypatch, mapping):
    def fake(name, *args, **kwargs):
        return mapping.get(name)

    monkeypatch.setattr(opencode.shutil, "which", fake)


def _resolved(bindir, name):
    return str(bindir / (name + ".bat" if os.name == "nt" else name))


def _stub_opencode(tmp_path, monkeypatch, calls, name="opencode2", exit_code=0):
    bindir = tmp_path / "bin"
    bindir.mkdir(exist_ok=True)
    var = f"MY_TEAM_TEST_CALLS_{name.upper()}"
    stub = bindir / f"stub_{name}.py"
    stub.write_text(
        "import json, os, sys\n"
        "from pathlib import Path\n"
        f"calls = Path(os.environ['{var}'])\n"
        "argv = sys.argv[1:]\n"
        'with calls.open("a", encoding="utf-8") as f:\n'
        '    f.write(json.dumps(argv) + "\\n")\n'
        f"sys.exit({exit_code})\n",
        encoding="utf-8",
    )
    (bindir / name).write_text("#!/usr/bin/env python3\n" + stub.read_text(encoding="utf-8"),
                               encoding="utf-8")
    (bindir / name).chmod(0o755)
    (bindir / f"{name}.bat").write_text(f'@"{sys.executable}" "{stub}" %*\n', encoding="utf-8")
    monkeypatch.setenv(var, str(calls))
    monkeypatch.setenv("PATH", str(bindir) + os.pathsep + os.environ.get("PATH", ""))


def _tree(path):
    return sorted((item.relative_to(path).as_posix(), item.read_text(encoding="utf-8"))
                  for item in path.rglob("*") if item.is_file())
