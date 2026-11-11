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
                conn.execute("PRAGMA journal_mode=WAL")
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
        rows = self.claim_batch(owner, 1, lease_seconds)
        return rows[0] if rows else None

    def claim_batch(self, owner: str, limit: int, lease_seconds: float = 60):
        if not 1 <= limit <= 100:
            raise ValueError("Audit claim batch must be between 1 and 100")
        now = time.time()
        with self._transaction() as conn:
            rows = conn.execute("""
                SELECT event_id, record, attempts FROM pending
                WHERE ready_at <= ? AND lease_until <= ? ORDER BY rowid LIMIT ?
            """, (now, now, limit)).fetchall()
            conn.executemany("UPDATE pending SET lease_owner = ?, lease_until = ? WHERE event_id = ?",
                             [(owner, now + lease_seconds, row[0]) for row in rows])
            return rows

    def acknowledge(self, event_id: str, owner: str):
        self.acknowledge_batch([event_id], owner)

    def acknowledge_batch(self, event_ids, owner: str):
        if not event_ids:
            return
        with self._transaction() as conn:
            placeholders = ','.join('?' for _ in event_ids)
            rows = conn.execute(f"SELECT event_id,size FROM pending WHERE event_id IN ({placeholders}) AND lease_owner = ?",
                               [*event_ids,owner]).fetchall()
            if rows:
                conn.executemany("DELETE FROM pending WHERE event_id = ?", [(r[0],) for r in rows])
                conn.execute("UPDATE usage SET events = events - ?, bytes = bytes - ? WHERE id = 1", (len(rows),sum(r[1] for r in rows)))

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
    def __init__(self, outbox, deliver, report, *, deliver_batch=None, batch_size=20,
                 batch_window_seconds=.05):
        if not 1 <= batch_size <= 100:
            raise ValueError("Audit delivery batch must be between 1 and 100")
        if not 0 <= batch_window_seconds <= .5:
            raise ValueError("Audit batch window must be between 0 and 0.5 seconds")
        self.outbox = outbox
        self.deliver = deliver
        self.report = report
        self.deliver_batch = deliver_batch
        self.batch_size = batch_size
        self.batch_window_seconds = batch_window_seconds
        self.owner = uuid.uuid4().hex
        self._stop = Event()
        self._wake = Event()
        self._thread = Thread(target=self._run, name="audit-db-delivery", daemon=True)

    def start(self):
        self._thread.start()

    def wake(self):
        self._wake.set()

    def run_once(self):
        if self.deliver_batch is not None:
            rows = self.outbox.claim_batch(self.owner, self.batch_size)
            if not rows:
                return False
            try:
                self.deliver_batch([row[1] for row in rows])
            except Exception:
                self.report("db_delivery_failed")
                for event_id, record, attempts in rows:
                    self.outbox.retry(event_id, self.owner, attempts)
            else:
                self.outbox.acknowledge_batch([row[0] for row in rows], self.owner)
            return True
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
            if self.deliver_batch is not None and self.batch_size > 1:
                # Records are already durable. Coalesce only after idle; an
                # existing backlog still drains full batches without this delay.
                # Event.wait holds no SQLite lock and shutdown interrupts it.
                self._stop.wait(self.batch_window_seconds)

    def stop(self, timeout: float = 1) -> bool:
        self._stop.set()
        self._wake.set()
        self._thread.join(timeout)
        return not self._thread.is_alive()
