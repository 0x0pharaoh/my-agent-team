import os
import subprocess
from pathlib import Path

from my_team.paths import ensure_private_dir, private_data_dir

_ENV_KEEP = ("PATH", "SYSTEMROOT", "SYSTEMDRIVE", "WINDIR", "HOME", "USERPROFILE", "HOMEDRIVE", "HOMEPATH",
             "TEMP", "TMP", "LANG", "LC_ALL", "COMSPEC", "PATHEXT", "SSH_AUTH_SOCK")


class GitError(Exception):
    pass


def _clean_env() -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if k.upper() in _ENV_KEEP}
    env.update(GIT_TERMINAL_PROMPT="0", GIT_OPTIONAL_LOCKS="0", GIT_PROTOCOL_FROM_USER="0")
    return env


def _empty_hooks_dir() -> Path:
    return ensure_private_dir(private_data_dir() / "empty-hooks")


def run(cwd: Path, *args: str, timeout: float = 10) -> str:
    safety = ["--no-pager", "-c", "core.fsmonitor=false", "-c", f"core.hooksPath={_empty_hooks_dir()}",
              "-c", "log.showSignature=false", "-c", "diff.external="]
    try:
        done = subprocess.run(["git", *safety, *args], cwd=cwd, env=_clean_env(), capture_output=True, text=True,
                              timeout=timeout, stdin=subprocess.DEVNULL)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise GitError(str(exc)) from exc
    if done.returncode != 0:
        raise GitError(done.stderr.strip())
    return done.stdout
