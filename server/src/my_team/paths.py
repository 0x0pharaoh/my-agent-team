import os
import subprocess
from pathlib import Path

import platformdirs

APP = "my-team"


def _home_override() -> Path | None:
    value = os.environ.get("MY_TEAM_HOME")
    return Path(value) if value else None


def data_dir() -> Path:
    override = _home_override()
    return override / "data" if override else Path(platformdirs.user_data_dir(APP, appauthor=False))


def config_dir() -> Path:
    override = _home_override()
    return override / "config" if override else Path(platformdirs.user_config_dir(APP, appauthor=False))


def ensure_private_dir(path: Path) -> Path:
    if not path.is_dir():
        path.mkdir(parents=True, mode=0o700)
        if os.name == "nt":
            _restrict_to_owner(path)
    return path


def _current_user_sid() -> str:
    line = subprocess.run(["whoami", "/user", "/fo", "csv", "/nh"], capture_output=True, text=True, check=True).stdout
    return line.strip().split(",")[-1].strip('"')


def _restrict_to_owner(path: Path) -> None:
    grants = [f"*{_current_user_sid()}:(OI)(CI)F", "*S-1-5-18:(OI)(CI)F"]
    subprocess.run(["icacls", str(path), "/inheritance:r", "/grant:r", *grants], capture_output=True, check=True)


def private_data_dir() -> Path:
    return ensure_private_dir(data_dir())


def projects_dir() -> Path:
    return ensure_private_dir(private_data_dir() / "projects")


def private_config_dir() -> Path:
    return ensure_private_dir(config_dir())
