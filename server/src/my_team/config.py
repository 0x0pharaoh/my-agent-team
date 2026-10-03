import os
import tomllib

from filelock import FileLock

from my_team.paths import private_config_dir

DEFAULT_PORT = 47300


def _path():
    return private_config_dir() / "config.toml"


def load() -> dict:
    try:
        data = tomllib.loads(_path().read_text(encoding="utf-8"))
    except FileNotFoundError:
        data = {}
    server = data.get("server", {})
    return {"autostart": bool(server.get("autostart", False)), "port": int(server.get("port", DEFAULT_PORT))}


def save(**changes) -> dict:
    with FileLock(str(_path()) + ".lock", timeout=10):
        current = load() | changes
        body = f"[server]\nautostart = {str(current['autostart']).lower()}\nport = {int(current['port'])}\n"
        tmp = _path().with_name(f".config.toml.{os.getpid()}.tmp")
        tmp.write_text(body, encoding="utf-8")
        os.replace(tmp, _path())
        return current


def port() -> int:
    override = os.environ.get("MY_TEAM_PORT")
    return int(override) if override else load()["port"]
