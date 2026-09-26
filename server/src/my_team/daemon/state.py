import asyncio
import secrets
import threading
from dataclasses import dataclass, field

from my_team import auth
from my_team.clock import now_ms
from my_team.db.engine import Database
from my_team.domain import projects
from my_team.errors import NotFound
from my_team.paths import private_data_dir, projects_dir


class Broadcaster:
    def __init__(self):
        self.loop: asyncio.AbstractEventLoop | None = None
        self._subscribers: dict[str, set[asyncio.Queue]] = {}

    def publish_threadsafe(self, project_id: str, batch: list[dict]) -> None:
        if self.loop and not self.loop.is_closed():
            self.loop.call_soon_threadsafe(self._publish, project_id, batch)

    def _publish(self, project_id: str, batch: list[dict]) -> None:
        for queue in self._subscribers.get(project_id, ()):
            queue.put_nowait((project_id, batch))

    def subscribe(self, project_ids: list[str]) -> asyncio.Queue:
        queue: asyncio.Queue = asyncio.Queue()
        for project_id in project_ids:
            self._subscribers.setdefault(project_id, set()).add(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue) -> None:
        for queues in self._subscribers.values():
            queues.discard(queue)

    @property
    def subscriber_count(self) -> int:
        return len({id(q) for queues in self._subscribers.values() for q in queues})


@dataclass
class DaemonState:
    port: int
    secret: bytes = field(default_factory=auth.load_secret)
    instance: str = field(default_factory=lambda: secrets.token_hex(8))
    started_ms: int = field(default_factory=now_ms)
    broadcaster: Broadcaster = field(default_factory=Broadcaster)
    nonces: auth.NonceCache = field(default_factory=auth.NonceCache)
    last_agent_ms: int = field(default_factory=now_ms)
    registry: Database = field(init=False)
    _projects: dict[str, Database] = field(default_factory=dict)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def __post_init__(self):
        self.registry = Database(private_data_dir() / "registry.db", "registry")

    @property
    def allowed_hosts(self) -> set[str]:
        return {f"127.0.0.1:{self.port}", f"localhost:{self.port}", f"[::1]:{self.port}"}

    @property
    def allowed_origins(self) -> set[str]:
        return {f"http://{host}" for host in self.allowed_hosts}

    def project_db(self, project_id: str) -> Database:
        with self._lock:
            db = self._projects.get(project_id)
            if db is None:
                path = projects_dir() / f"{project_id}.db"
                db = Database(path, "project",
                              on_commit=lambda batch: self.broadcaster.publish_threadsafe(project_id, batch))
                db.write_sync(_ensure_epoch)
                self._projects[project_id] = db
            return db

    async def project(self, project_id: str) -> dict:
        info = await self.registry.read(lambda tx: projects.get(tx, project_id))
        if not info["roots"]:
            raise NotFound("unknown_project", "Project has no bound root.")
        return info | {"root": info["roots"][0]}

    def close(self) -> None:
        for db in self._projects.values():
            db.close()
        self.registry.close()


def _ensure_epoch(tx) -> str:
    epoch = tx.scalar("SELECT value FROM meta WHERE key = 'db_epoch'")
    if epoch is None:
        epoch = secrets.token_hex(4)
        tx.execute("INSERT INTO meta (key, value) VALUES ('db_epoch', ?)", (epoch,))
    return epoch


def db_epoch(tx) -> str:
    return tx.scalar("SELECT value FROM meta WHERE key = 'db_epoch'")
