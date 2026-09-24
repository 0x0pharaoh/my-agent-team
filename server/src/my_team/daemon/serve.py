import asyncio
import errno
import json
import logging
import os
import socket
import sys

import uvicorn
from filelock import FileLock, Timeout

from my_team import __version__
from my_team.clock import now_ms
from my_team.config import port as configured_port
from my_team.daemon.app import API_VERSION, create_app
from my_team.daemon.state import DaemonState
from my_team.paths import private_data_dir

IDLE_SHUTDOWN_MS = 2 * 3600_000
log = logging.getLogger("my_team.daemon")
_NO_IPV6 = {errno.EADDRNOTAVAIL, errno.EAFNOSUPPORT, getattr(errno, "WSAEADDRNOTAVAIL", -1),
            getattr(errno, "WSAEAFNOSUPPORT", -1)}


class PortInUse(Exception):
    pass


def bind_loopback(port: int) -> list[socket.socket]:
    bound = []
    for family, host in ((socket.AF_INET, "127.0.0.1"), (socket.AF_INET6, "::1")):
        try:
            sock = socket.socket(family, socket.SOCK_STREAM)
        except OSError:
            continue
        if os.name == "nt":
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        else:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        if family == socket.AF_INET6:
            sock.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 1)
        try:
            sock.bind((host, port))
        except OSError as exc:
            sock.close()
            if family == socket.AF_INET6 and exc.errno in _NO_IPV6:
                continue
            for other in bound:
                other.close()
            raise PortInUse(f"port {port} on {host} is held by another process") from exc
        sock.listen(128)
        sock.setblocking(False)
        bound.append(sock)
    return bound


def server_file():
    return private_data_dir() / "server.json"


def write_server_file(state: DaemonState) -> None:
    path = server_file()
    tmp = path.with_name(f".server.json.{os.getpid()}.tmp")
    tmp.write_text(json.dumps({"port": state.port, "pid": os.getpid(), "instance": state.instance,
                               "version": __version__, "api_version": API_VERSION, "started_ms": state.started_ms}),
                   encoding="utf-8")
    os.replace(tmp, path)


def remove_server_file(instance: str) -> None:
    path = server_file()
    try:
        if json.loads(path.read_text(encoding="utf-8")).get("instance") == instance:
            path.unlink()
    except (FileNotFoundError, ValueError):
        pass


async def _watch_idle(server: uvicorn.Server, state: DaemonState) -> None:
    while not server.should_exit:
        await asyncio.sleep(30)
        if state.broadcaster.subscriber_count == 0 and now_ms() - state.last_agent_ms > IDLE_SHUTDOWN_MS:
            log.info("idle_shutdown")
            server.should_exit = True


async def _serve(server: uvicorn.Server, state: DaemonState, sockets: list[socket.socket]) -> None:
    state.broadcaster.loop = asyncio.get_running_loop()
    watcher = asyncio.create_task(_watch_idle(server, state))
    try:
        await server.serve(sockets=sockets)
    finally:
        watcher.cancel()


def run(port: int | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    lock = FileLock(str(private_data_dir() / "serve.lock"))
    try:
        lock.acquire(timeout=0)
    except Timeout:
        print("my-team daemon is already running", file=sys.stderr)
        return 3
    try:
        port = port or configured_port()
        try:
            sockets = bind_loopback(port)
        except PortInUse as exc:
            print(f"my-team: {exc}. Stop that process or choose another port with `my-team serve --port`.",
                  file=sys.stderr)
            return 4
        state = DaemonState(port=port)
        config = uvicorn.Config(create_app(state), log_level="warning", access_log=False, lifespan="off",
                                timeout_graceful_shutdown=10)
        server = uvicorn.Server(config)
        write_server_file(state)
        try:
            asyncio.run(_serve(server, state, sockets))
        finally:
            remove_server_file(state.instance)
            state.close()
        return 0
    finally:
        lock.release()
