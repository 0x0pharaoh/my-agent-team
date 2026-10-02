import pytest

from my_team import __version__
from my_team.cli import main


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
