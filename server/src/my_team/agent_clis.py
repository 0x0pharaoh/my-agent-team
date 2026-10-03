import shutil
import subprocess
import time

from my_team.installers.hermes import _hermes_exe

RESOLVERS = {
    "claude-code": lambda: shutil.which("claude"),
    "codex": lambda: shutil.which("codex"),
    # opencode2 is the V2-only shim; plain `opencode` can be the V1 binary.
    "opencode": lambda: shutil.which("opencode2") or shutil.which("opencode"),
    "hermes": lambda: shutil.which("hermes") or _hermes_exe(),
}
_cache: tuple[float, list[dict]] | None = None


def resolve(agent_type: str) -> str | None:
    return RESOLVERS[agent_type]()


def _version(path: str) -> str | None:
    try:
        done = subprocess.run([path, "--version"], capture_output=True, text=True, timeout=20,
                              stdin=subprocess.DEVNULL)
    except (OSError, subprocess.TimeoutExpired):
        return None
    lines = (done.stdout or done.stderr or "").splitlines()
    return next((line.strip()[:80] for line in lines if any(c.isdigit() for c in line)), None)


def detect(max_age_s: float = 60) -> list[dict]:
    """Installed agent CLIs; `--version` spawns are slow, so results are cached briefly."""
    global _cache
    if _cache and time.monotonic() - _cache[0] < max_age_s:
        return _cache[1]
    found = []
    for agent_type in RESOLVERS:
        path = resolve(agent_type)
        found.append({"agent_type": agent_type, "installed": bool(path), "path": path,
                      "version": _version(path) if path else None})
    _cache = (time.monotonic(), found)
    return found
