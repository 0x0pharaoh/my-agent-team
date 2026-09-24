import asyncio
import concurrent.futures
import logging
import queue
import sqlite3
import threading
from collections.abc import Callable
from importlib import resources
from pathlib import Path
from typing import Any

log = logging.getLogger("my_team.db")

PRAGMAS = ("journal_mode=WAL", "synchronous=FULL", "foreign_keys=ON", "busy_timeout=5000", "trusted_schema=OFF",
           "secure_delete=ON")


class SchemaTooNew(Exception):
    pass


class Tx:
    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn
        self.events: list[dict] = []

    def execute(self, sql: str, params=()) -> sqlite3.Cursor:
        return self.conn.execute(sql, params)

    def one(self, sql: str, params=()) -> sqlite3.Row | None:
        return self.conn.execute(sql, params).fetchone()

    def all(self, sql: str, params=()) -> list[sqlite3.Row]:
        return self.conn.execute(sql, params).fetchall()

    def scalar(self, sql: str, params=()) -> Any:
        row = self.conn.execute(sql, params).fetchone()
        return row[0] if row else None


def connect(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(path, isolation_level=None)
    conn.row_factory = sqlite3.Row
    for pragma in PRAGMAS:
        conn.execute(f"PRAGMA {pragma}")
    return conn


def _migrations(package: str) -> list[tuple[int, str]]:
    root = resources.files(f"my_team.db.migrations.{package}")
    found = [(int(entry.name.split("_", 1)[0]), entry.read_text(encoding="utf-8"))
             for entry in root.iterdir() if entry.name.endswith(".sql")]
    return sorted(found)


def migrate(conn: sqlite3.Connection, package: str) -> int:
    steps = _migrations(package)
    current = conn.execute("PRAGMA user_version").fetchone()[0]
    latest = steps[-1][0] if steps else 0
    if current > latest:
        raise SchemaTooNew(f"database schema {current} is newer than this my-team ({latest})")
    for number, sql in steps:
        if number <= current:
            continue
        conn.execute("PRAGMA foreign_keys=OFF")
        try:
            conn.executescript(f"BEGIN IMMEDIATE;\n{sql}\nPRAGMA user_version = {number};\nCOMMIT;")
        except sqlite3.Error:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            raise
        finally:
            conn.execute("PRAGMA foreign_keys=ON")
        if conn.execute("PRAGMA foreign_key_check").fetchone():
            raise sqlite3.IntegrityError(f"migration {number} left foreign key violations")
    return latest


class Database:
    """Single thread owning one connection; every read and write runs there, in submission order."""

    def __init__(self, path: Path, migrations: str, on_commit: Callable[[list[dict]], None] | None = None):
        self.path = path
        self.on_commit = on_commit
        self._jobs: queue.SimpleQueue = queue.SimpleQueue()
        ready: concurrent.futures.Future = concurrent.futures.Future()
        self._thread = threading.Thread(target=self._serve, args=(migrations, ready), daemon=True,
                                        name=f"db:{path.name}")
        self._thread.start()
        ready.result()

    def _serve(self, migrations: str, ready: concurrent.futures.Future) -> None:
        try:
            conn = connect(self.path)
            migrate(conn, migrations)
        except BaseException as exc:
            ready.set_exception(exc)
            return
        ready.set_result(None)
        while (job := self._jobs.get()) is not None:
            fn, write, future = job
            if future.set_running_or_notify_cancel():
                self._execute(conn, fn, write, future)
        conn.close()

    def _execute(self, conn, fn, write, future) -> None:
        tx = Tx(conn)
        try:
            if write:
                conn.execute("BEGIN IMMEDIATE")
            result = fn(tx)
            if write:
                conn.execute("COMMIT")
        except BaseException as exc:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            future.set_exception(exc)
            return
        future.set_result(result)
        if tx.events and self.on_commit:
            try:
                self.on_commit(tx.events)
            except Exception:
                log.exception("db_on_commit_failed", extra={"db": self.path.name})

    def submit(self, fn: Callable[[Tx], Any], write: bool) -> concurrent.futures.Future:
        future: concurrent.futures.Future = concurrent.futures.Future()
        self._jobs.put((fn, write, future))
        return future

    def read_sync(self, fn: Callable[[Tx], Any]) -> Any:
        return self.submit(fn, False).result()

    def write_sync(self, fn: Callable[[Tx], Any]) -> Any:
        return self.submit(fn, True).result()

    async def read(self, fn: Callable[[Tx], Any]) -> Any:
        return await asyncio.wrap_future(self.submit(fn, False))

    async def write(self, fn: Callable[[Tx], Any]) -> Any:
        return await asyncio.wrap_future(self.submit(fn, True))

    def close(self) -> None:
        self._jobs.put(None)
        self._thread.join(timeout=10)
