from my_team import activation


def test_default_is_off(tmp_path):
    assert activation.resolve(activation.empty(), "claude-code", "s1", tmp_path)["active"] is False


def test_global_on_applies_everywhere(tmp_path):
    activation.set_scope("global", True, cwd=tmp_path)
    assert activation.resolve(activation.load(), "codex", "t1", tmp_path)["scope"] == "global"


def test_deepest_directory_wins_and_narrower_off_beats_broader_on(tmp_path):
    inner = tmp_path / "a" / "b"
    inner.mkdir(parents=True)
    activation.set_scope("global", True, cwd=tmp_path)
    activation.set_scope("directory", True, cwd=tmp_path, path=str(tmp_path / "a"))
    activation.set_scope("directory", False, cwd=tmp_path, path=str(inner))
    state = activation.load()
    assert activation.resolve(state, "claude-code", "s1", tmp_path / "a")["active"] is True
    assert activation.resolve(state, "claude-code", "s1", inner)["active"] is False


def test_directory_match_needs_a_path_boundary(tmp_path):
    (tmp_path / "app").mkdir()
    (tmp_path / "application").mkdir()
    activation.set_scope("directory", True, cwd=tmp_path, path=str(tmp_path / "app"))
    assert activation.resolve(activation.load(), "claude-code", None, tmp_path / "application")["active"] is False


def test_session_off_overrides_directory_on_for_that_session_only(tmp_path):
    activation.set_scope("directory", True, cwd=tmp_path, path=str(tmp_path))
    activation.set_scope("session", False, cwd=tmp_path, agent_type="claude-code", native_id="s1")
    state = activation.load()
    assert activation.resolve(state, "claude-code", "s1", tmp_path)["active"] is False
    assert activation.resolve(state, "claude-code", "s2", tmp_path)["active"] is True


def test_one_time_carries_its_task_and_can_end(tmp_path):
    activation.set_scope("one-time", True, cwd=tmp_path, agent_type="codex", native_id="t1", task="fix the build")
    resolved = activation.resolve(activation.load(), "codex", "t1", tmp_path)
    assert resolved == {"active": True, "scope": "one-time", "task": "fix the build"}
    activation.set_scope("one-time", False, cwd=tmp_path, agent_type="codex", native_id="t1")
    assert activation.resolve(activation.load(), "codex", "t1", tmp_path)["active"] is False
