import os
import signal
import subprocess
import sys

import pytest

from my_team import __version__
from my_team.cli import _stop_daemon, main


def test_version_flag_prints_version(capsys):
    with pytest.raises(SystemExit) as exit_info:
        main(["--version"])
    assert exit_info.value.code == 0
    assert capsys.readouterr().out.strip() == f"my-team {__version__}"


def test_call_daemon_down_prints_one_line(monkeypatch, capsys):
    from my_team.client import DaemonUnavailable

    def _down(*args, **kwargs):
        raise DaemonUnavailable("my-team server is not running: run `my-team serve`")

    monkeypatch.setattr("my_team.client.connect", _down)
    assert main(["call", "team_context"]) == 1
    out = capsys.readouterr().out.strip()
    assert out.count("\n") == 0 and "my-team serve" in out


def _seed_sessions(home):
    import sqlite3
    data = home / "data"
    (data / "projects").mkdir(parents=True)
    with sqlite3.connect(data / "registry.db") as conn:
        conn.execute("CREATE TABLE projects (id TEXT PRIMARY KEY)")
        conn.execute("INSERT INTO projects (id) VALUES ('p1')")
        conn.commit()
    with sqlite3.connect(data / "projects" / "p1.db") as conn:
        conn.execute("CREATE TABLE sessions (agent_type TEXT, native_session_id TEXT, root_path TEXT,"
                     " last_heartbeat_ms INTEGER, ended_ms INTEGER)")
        conn.execute("INSERT INTO sessions VALUES ('opencode', 'n1', '/repo', 1000, NULL)")
        conn.execute("INSERT INTO sessions VALUES ('codex', 'n2', '/repo', 2000, 3000)")
        conn.commit()


def test_upgrade_lists_sessions_and_runs_uv_tool(home, monkeypatch, capsys):
    _seed_sessions(home)
    calls = []
    monkeypatch.setattr("shutil.which", lambda name: "/fake/uv")
    monkeypatch.setattr("subprocess.run",
                        lambda argv, **kwargs: calls.append(argv) or subprocess.CompletedProcess(argv, 0))
    assert main(["upgrade"]) == 0
    assert calls == [["/fake/uv", "tool", "upgrade", "my-team-agents"]]
    out = capsys.readouterr().out
    assert "opencode n1 in /repo" in out and "codex" not in out


def test_upgrade_refuses_without_uv(home, monkeypatch, capsys):
    monkeypatch.setattr("shutil.which", lambda name: None)
    assert main(["upgrade"]) == 1
    assert "uv not found" in capsys.readouterr().out


def test_upgrade_decline_keeps_daemon(home, monkeypatch):
    from my_team import client as client_module
    proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])

    def _boom(*args, **kwargs):
        if args[0][0] == "/fake/uv":
            raise AssertionError("uv must not run")
        return _real_run(*args, **kwargs)

    _real_run = subprocess.run
    try:
        monkeypatch.setattr(client_module, "_server_info", lambda: {"pid": proc.pid})
        monkeypatch.setattr("builtins.input", lambda *args: "n")
        monkeypatch.setattr("subprocess.run", _boom)
        assert main(["upgrade"]) == 1
        assert proc.poll() is None
    finally:
        proc.terminate()


def test_stop_daemon_kills_and_accepts_dead_pid():
    launcher = subprocess.Popen(
        [sys.executable, "-c", "import subprocess, sys; "
         "p = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'],"
         " stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL);"
         " print(p.pid, flush=True)"],
        stdout=subprocess.PIPE, text=True,
        creationflags=getattr(subprocess, "DETACHED_PROCESS", 0))
    orphan = int(launcher.communicate(timeout=30)[0].strip())
    try:
        assert _stop_daemon(orphan, timeout_s=10) is True
    finally:
        try:
            os.kill(orphan, signal.SIGTERM)
        except OSError:
            pass
    assert _stop_daemon(999999999, timeout_s=0.1) is True
