import os
import sys
from pathlib import Path

from my_team import client


def test_daemon_spawns_without_a_console_window_on_windows(monkeypatch):
    launched = []
    monkeypatch.setattr(client.subprocess, "Popen", lambda command, **kwargs: launched.append(command))
    client.spawn_daemon()
    gui = Path(sys.executable).with_name("pythonw.exe")
    expected = str(gui) if os.name == "nt" and gui.is_file() else sys.executable
    assert launched[0][:4] == [expected, "-m", "my_team", "serve"]
