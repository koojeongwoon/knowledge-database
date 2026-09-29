"""Bounded local audit delivery backlog. Network I/O never holds the SQLite lock."""

import os
import sqlite3
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from threading import Event, RLock, Thread


class AuditOutbox:
    def __init__(self, path: Path, max_bytes: int, max_events: int):
        self.path = Path(path)
        self.max_bytes = max_bytes
        self.max_events = max_events
        self._lock = RLock()
        self._conn = None

    def _connection(self):
        if self._conn is None:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            fd = os.open(self.path, os.O_CREAT | os.O_WRONLY, 0o600)
            os.close(fd)
            conn = sqlite3.connect(self.path, timeout=1, check_same_thread=False)
            try:
                conn.execute("PRAGMA synchronous=FULL")
                conn.execute("PRAGMA cache_size=-1024")
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS pending (
                        event_id TEXT PRIMARY KEY, record TEXT NOT NULL, size INTEGER NOT NULL,
                        attempts INTEGER NOT NULL DEFAULT 0, ready_at REAL NOT NULL DEFAULT 0,
                        lease_owner TEXT, lease_until REAL NOT NULL DEFAULT 0
                    )
                """)
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS usage (
                        id INTEGER PRIMARY KEY CHECK (id = 1), events INTEGER NOT NULL,
                        bytes INTEGER NOT NULL
                    )
                """)
                conn.execute("INSERT OR IGNORE INTO usage VALUES (1, 0, 0)")
                conn.commit()
            except BaseException:
                conn.close()
                raise
            self._conn = conn
        return self._conn

    @contextmanager
    def _transaction(self):
        with self._lock:
            conn = self._connection()
            conn.execute("BEGIN IMMEDIATE")
            try:
                yield conn
                conn.commit()
            except BaseException:
                conn.rollback()
                raise

    def put(self, event_id: str, record: str) -> bool:
        size = len(record.encode("utf-8"))
        with self._transaction() as conn:
            events, used_bytes = conn.execute("SELECT events, bytes FROM usage WHERE id = 1").fetchone()
            if events >= self.max_events or used_bytes + size > self.max_bytes:
                return False
            conn.execute("INSERT INTO pending(event_id, record, size) VALUES (?, ?, ?)", (event_id, record, size))
            conn.execute("UPDATE usage SET events = events + 1, bytes = bytes + ? WHERE id = 1", (size,))
        return True

    def claim(self, owner: str, lease_seconds: float = 60):
        now = time.time()
        with self._transaction() as conn:
            row = conn.execute("""
                SELECT event_id, record, attempts FROM pending
                WHERE ready_at <= ? AND lease_until <= ? ORDER BY rowid LIMIT 1
            """, (now, now)).fetchone()
            if row:
                conn.execute("UPDATE pending SET lease_owner = ?, lease_until = ? WHERE event_id = ?",
                             (owner, now + lease_seconds, row[0]))
            return row

    def acknowledge(self, event_id: str, owner: str):
        with self._transaction() as conn:
            row = conn.execute("SELECT size FROM pending WHERE event_id = ? AND lease_owner = ?",
                               (event_id, owner)).fetchone()
            if row:
                conn.execute("DELETE FROM pending WHERE event_id = ?", (event_id,))
                conn.execute("UPDATE usage SET events = events - 1, bytes = bytes - ? WHERE id = 1", (row[0],))

    def retry(self, event_id: str, owner: str, attempts: int):
        delay = min(60, 2 ** min(attempts, 6))
        with self._transaction() as conn:
            conn.execute("""
                UPDATE pending SET attempts = attempts + 1, ready_at = ?,
                                   lease_owner = NULL, lease_until = 0
                WHERE event_id = ? AND lease_owner = ?
            """, (time.time() + delay, event_id, owner))

    def usage(self):
        with self._lock:
            return self._connection().execute("SELECT events, bytes FROM usage WHERE id = 1").fetchone()

    def close(self):
        with self._lock:
            if self._conn is not None:
                self._conn.close()
                self._conn = None


class AuditDeliveryWorker:
    def __init__(self, outbox, deliver, report):
        self.outbox = outbox
        self.deliver = deliver
        self.report = report
        self.owner = uuid.uuid4().hex
        self._stop = Event()
        self._wake = Event()
        self._thread = Thread(target=self._run, name="audit-db-delivery", daemon=True)

    def start(self):
        self._thread.start()

    def wake(self):
        self._wake.set()

    def run_once(self):
        row = self.outbox.claim(self.owner)
        if row is None:
            return False
        event_id, record, attempts = row
        try:
            self.deliver(record)
        except Exception:
            self.report("db_delivery_failed")
            self.outbox.retry(event_id, self.owner, attempts)
        else:
            self.outbox.acknowledge(event_id, self.owner)
        return True

    def _run(self):
        while not self._stop.is_set():
            try:
                if self.run_once():
                    continue
            except Exception:
                self.report("outbox_worker_failed")
            self._wake.wait(0.5)
            self._wake.clear()

    def stop(self, timeout: float = 1) -> bool:
        self._stop.set()
        self._wake.set()
        self._thread.join(timeout)
        return not self._thread.is_alive()
