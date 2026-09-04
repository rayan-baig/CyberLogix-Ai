"""SQLite persistence for the CyberLogix hub.

The platform's queries are all small in-memory scans over a single tenant's
estate, so this is a document store rather than a relational schema: every
entity is one JSON row keyed by (kind, id). That keeps durability a
write-through concern instead of a migration burden, and it means the
in-memory working set in `store.py` stays the fast path.

A SQLite file on the instance's disk costs nothing to run and survives a
restart. Point CYBERLOGIX_DB_PATH at a mounted volume to survive the
container, or swap this class for a Postgres adapter to scale out.
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import threading
from typing import Any, Dict, Iterable, List, Optional, Tuple

logger = logging.getLogger("cyberlogix.db")

DEFAULT_DB_PATH = os.environ.get("CYBERLOGIX_DB_PATH", "cyberlogix.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS records (
    kind    TEXT NOT NULL,
    rec_id  TEXT NOT NULL,
    data    TEXT NOT NULL,
    PRIMARY KEY (kind, rec_id)
);
CREATE INDEX IF NOT EXISTS records_kind ON records (kind);
"""


class Database:
    """A tiny durable key/document store over SQLite."""

    def __init__(self, path: str = DEFAULT_DB_PATH) -> None:
        self.path = path
        self._lock = threading.RLock()
        # The store is shared across FastAPI's threadpool workers, so the
        # connection must be usable from any thread; the lock serialises it.
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.executescript(SCHEMA)
        if path != ":memory:":
            # WAL keeps readers off the writer's back and survives a crash.
            self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=NORMAL")
        self._conn.commit()
        logger.info("SQLite persistence open at %s", path)

    def put(self, kind: str, rec_id: str, data: Dict[str, Any]) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO records (kind, rec_id, data) VALUES (?, ?, ?) "
                "ON CONFLICT(kind, rec_id) DO UPDATE SET data = excluded.data",
                (kind, rec_id, json.dumps(data)),
            )
            self._conn.commit()

    def put_many(self, kind: str, rows: Iterable[Tuple[str, Dict[str, Any]]]) -> None:
        payload = [(kind, rec_id, json.dumps(data)) for rec_id, data in rows]
        if not payload:
            return
        with self._lock:
            self._conn.executemany(
                "INSERT INTO records (kind, rec_id, data) VALUES (?, ?, ?) "
                "ON CONFLICT(kind, rec_id) DO UPDATE SET data = excluded.data",
                payload,
            )
            self._conn.commit()

    def get(self, kind: str, rec_id: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            row = self._conn.execute(
                "SELECT data FROM records WHERE kind = ? AND rec_id = ?",
                (kind, rec_id),
            ).fetchone()
        return json.loads(row[0]) if row else None

    def delete(self, kind: str, rec_id: str) -> None:
        with self._lock:
            self._conn.execute(
                "DELETE FROM records WHERE kind = ? AND rec_id = ?", (kind, rec_id)
            )
            self._conn.commit()

    def delete_many(self, kind: str, rec_ids: Iterable[str]) -> None:
        ids = list(rec_ids)
        if not ids:
            return
        with self._lock:
            self._conn.executemany(
                "DELETE FROM records WHERE kind = ? AND rec_id = ?",
                [(kind, rec_id) for rec_id in ids],
            )
            self._conn.commit()

    def all(self, kind: str) -> List[Dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT data FROM records WHERE kind = ?", (kind,)
            ).fetchall()
        return [json.loads(row[0]) for row in rows]

    def count(self, kind: str) -> int:
        with self._lock:
            row = self._conn.execute(
                "SELECT COUNT(*) FROM records WHERE kind = ?", (kind,)
            ).fetchone()
        return int(row[0])

    def clear(self) -> None:
        with self._lock:
            self._conn.execute("DELETE FROM records")
            self._conn.commit()

    def close(self) -> None:
        with self._lock:
            self._conn.close()
