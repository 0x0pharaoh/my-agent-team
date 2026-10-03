import json

from my_team.installers import codex


def test_codex_install_is_idempotent_and_preserves_hooks(tmp_path, monkeypatch):
    home = tmp_path / "home"
    codex_home = tmp_path / "codex"
    my_team_home = tmp_path / "my-team"
    skill = tmp_path / "skill"
    (skill / "references").mkdir(parents=True)
    (skill / "SKILL.md").write_text("my-team skill\n", encoding="utf-8")
    (skill / "references" / "rules.md").write_text("rules\n", encoding="utf-8")
    hooks_path = codex_home / "hooks.json"
    hooks_path.parent.mkdir(parents=True)
    original = {
        "hooks": {
            "UserPromptSubmit": [{"hooks": [{"type": "command", "command": "other", "args": ["x"]}]}],
            "PreToolUse": [{"hooks": [{"type": "command", "command": "keep"}]}],
        }
    }
    hooks_path.write_text(json.dumps(original), encoding="utf-8")
    calls = []

    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("CODEX_HOME", str(codex_home))
    monkeypatch.setenv("MY_TEAM_HOME", str(my_team_home))
    monkeypatch.setattr("my_team.installers.skill_source", lambda: skill)
    monkeypatch.setattr(codex, "cli_path", lambda: str(tmp_path / "bin" / "my-team.exe"))
    monkeypatch.setattr(codex, "_run", lambda args, **kwargs: fake_run(args, calls))

    assert codex.install(yes=True) == 0
    first_hooks = hooks_path.read_text(encoding="utf-8")
    first_skill = tree(home / ".agents" / "skills" / "my-team")
    assert codex.install(yes=True) == 0

    data = json.loads(hooks_path.read_text(encoding="utf-8"))
    assert hooks_path.read_text(encoding="utf-8") == first_hooks
    assert tree(home / ".agents" / "skills" / "my-team") == first_skill
    assert data["hooks"]["PreToolUse"] == original["hooks"]["PreToolUse"]
    assert data["hooks"]["UserPromptSubmit"][0] == original["hooks"]["UserPromptSubmit"][0]
    assert len(data["hooks"]["UserPromptSubmit"]) == 2
    assert len(data["hooks"]["SessionStart"]) == 1
    assert calls == [
        ["codex", "mcp", "get", "my-team"],
        ["codex", "mcp", "add", "my-team", "--", str(tmp_path / "bin" / "my-team.exe"), "mcp"],
        ["codex", "mcp", "get", "my-team"],
    ]
    assert (my_team_home / "config" / "config.toml").read_text(encoding="utf-8").splitlines()[1] == "autostart = true"


def fake_run(args, calls):
    calls.append(args)

    class Result:
        returncode = 0 if args[:3] == ["codex", "mcp", "get"] and len(calls) > 2 else 1

    return Result()


def tree(path):
    return sorted((item.relative_to(path).as_posix(), item.read_text(encoding="utf-8")) for item in path.rglob("*") if item.is_file())


def test_codex_hooks_use_shell_strings_and_replace_old_entries(tmp_path, monkeypatch):
    codex_home = tmp_path / "codex"
    hooks_path = codex_home / "hooks.json"
    hooks_path.parent.mkdir(parents=True)
    old = {"type": "command", "command": "C:/x/my-team.exe", "args": ["hook", "prompt", "--agent", "codex"]}
    hooks_path.write_text(json.dumps({"hooks": {"UserPromptSubmit": [{"hooks": [old]}]}}), encoding="utf-8")
    codex._write_hooks(codex_home, "C:/Program Files/my team/my-team.exe")
    hooks = json.loads(hooks_path.read_text(encoding="utf-8"))["hooks"]
    start = hooks["SessionStart"][0]["hooks"][0]
    assert "args" not in start and start["command"].endswith("hook session-start --agent codex")
    assert start["commandWindows"] == "& 'C:/Program Files/my team/my-team.exe' hook session-start --agent codex"
    prompt = hooks["UserPromptSubmit"][0]["hooks"][0]
    assert prompt["type"] == "command" and prompt["command"].endswith("hook prompt --agent codex")
    assert "mcp_tool" not in hooks_path.read_text(encoding="utf-8")


def test_deny_hook_written_only_with_flag_and_removed_without(tmp_path, monkeypatch):
    codex_home = tmp_path / "codex"
    hooks_path = codex_home / "hooks.json"
    hooks_path.parent.mkdir(parents=True)
    hooks_path.write_text(json.dumps({"hooks": {}}), encoding="utf-8")
    monkeypatch.setenv("MY_TEAM_CODEX_DENY_UNCLAIMED", "1")
    codex._write_hooks(codex_home, "C:/t/my-team.exe", deny=True)
    pre = json.loads(hooks_path.read_text(encoding="utf-8"))["hooks"]["PreToolUse"]
    assert len(pre) == 1 and pre[0]["matcher"] == "apply_patch|Edit|Write"
    assert pre[0]["hooks"][0]["command"].endswith("hook pre-edit --agent codex")
    monkeypatch.delenv("MY_TEAM_CODEX_DENY_UNCLAIMED")
    codex._write_hooks(codex_home, "C:/t/my-team.exe")
    assert "PreToolUse" not in json.loads(hooks_path.read_text(encoding="utf-8"))["hooks"]


def test_install_wires_deny_flag_to_write_hooks(tmp_path, monkeypatch):
    seen = {}
    monkeypatch.setenv("MY_TEAM_CODEX_DENY_UNCLAIMED", "1")
    monkeypatch.setattr(codex, "cli_path", lambda: "C:/t/my-team.exe")
    monkeypatch.setattr(codex, "_write_hooks",
                        lambda home, cli, deny=False: seen.setdefault("deny", deny))
    monkeypatch.setattr(codex, "_add_mcp", lambda cli: None)
    monkeypatch.setattr(codex, "copy_skill", lambda target: True)
    monkeypatch.setattr(codex.config, "save", lambda **kwargs: None)
    monkeypatch.setattr(codex, "confirm", lambda plan, yes: True)
    assert codex.install(yes=True) == 0
    assert seen["deny"] is True
