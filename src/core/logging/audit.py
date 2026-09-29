import atexit
import datetime
import hashlib
import json
import logging
import queue
import time
import uuid
from collections import Counter
from logging.handlers import QueueHandler, QueueListener, TimedRotatingFileHandler
from pathlib import Path
from threading import Lock
from typing import Any, Dict

import psycopg

from src.core.config import DATABASE_URL, settings
from src.core.logging.outbox import AuditDeliveryWorker, AuditOutbox
from src.core.security.sanitizer import sanitize_dict, sanitize_text


class PostgresAuditHandler(logging.Handler):
    def __init__(self, connect=None):
        super().__init__()
        self._connect = connect or psycopg.connect

    def emit(self, record):
        msg = record.getMessage()
        if not msg.startswith("[AUDIT] "):
            return
        data = json.loads(msg[len("[AUDIT] "):])
        payload = {
            **data["payload"],
            "_audit_delivery": {"event_id": data["event_id"], "recorded_at": data["timestamp"]},
        }
        # This worker owns its connection and timeout budget, independent of the
        # application's shared pool. Failure propagates to durable retry.
        with self._connect(
            DATABASE_URL, connect_timeout=2,
            options="-c statement_timeout=3000 -c lock_timeout=1000",
        ) as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO knowledge_audit_logs (user_id, action, status, payload)
                    VALUES (%s, %s, %s, %s)
                """, (data["user_id"], data["action"], data["status"],
                      json.dumps(payload, ensure_ascii=False)))


class AuditDiagnostics:
    def __init__(self):
        self._lock = Lock()
        self._counts = Counter()
        self._last_warning = {}

    def report(self, reason):
        with self._lock:
            self._counts[reason] += 1
            now = time.monotonic()
            warn = now - self._last_warning.get(reason, float("-inf")) >= 60
            if warn:
                self._last_warning[reason] = now
            count = self._counts[reason]
        if warn:
            # No record content, credential, or underlying exception is logged.
            logging.getLogger("audit_delivery").warning("Audit delivery: reason=%s count=%d", reason, count)

    def snapshot(self):
        with self._lock:
            return dict(self._counts)


class AuditFileHandler(TimedRotatingFileHandler):
    def handleError(self, record):
        # Logging's default handler would print the full record on I/O failure.
        diagnostics.report("file_write_failed")


class BoundedAuditQueueHandler(QueueHandler):
    def __init__(self, log_queue, outbox, worker, local_handlers, diagnostics):
        super().__init__(log_queue)
        self.outbox = outbox
        self.worker = worker
        self.local_handlers = local_handlers
        self.diagnostics = diagnostics

    def enqueue(self, record):
        body = record.getMessage()[len("[AUDIT] "):]
        data = None
        saved = False
        try:
            data = json.loads(body)
            saved = self.outbox.put(data["event_id"], body)
            if saved:
                self.worker.wake()
            else:
                self.diagnostics.report("outbox_full_file_only")
        except Exception:
            self.diagnostics.report("outbox_write_failed_file_only")
        if not saved and isinstance(data, dict):
            # Persist the degradation on the individual file record as well as
            # reporting aggregate counters. The producer reserves this field.
            data["db_delivery"] = "file_only"
            record.msg = "[AUDIT] " + json.dumps(data, ensure_ascii=False)
            record.args = None
        try:
            self.queue.put_nowait(record)
        except queue.Full:
            self.diagnostics.report("file_queue_full_sync_write")
            # Local backpressure preserves the record without allocating another
            # queue or doing network I/O on the request path.
            for handler in self.local_handlers:
                handler.handle(record)


class BoundedAuditQueueListener(QueueListener):
    def stop(self, timeout=1):
        thread = self._thread
        if thread is None:
            return True
        try:
            self.queue.put(self._sentinel, timeout=timeout)
        except queue.Full:
            return False
        thread.join(timeout)
        if not thread.is_alive():
            self._thread = None
            return True
        return False


LOG_DIR = Path("logs")
LOG_DIR.mkdir(parents=True, exist_ok=True)
AUDIT_LOG_FILE = LOG_DIR / "audit.log"
logger = logging.getLogger("audit")
logger.setLevel(logging.INFO)
logger.handlers.clear()
formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
stream_handler = logging.StreamHandler()
stream_handler.setLevel(logging.INFO)
stream_handler.setFormatter(formatter)
diagnostics = AuditDiagnostics()
file_handler = AuditFileHandler(
    filename=str(AUDIT_LOG_FILE), when="midnight", interval=1,
    backupCount=30, encoding="utf-8",
)
file_handler.setLevel(logging.INFO)
file_handler.setFormatter(formatter)
db_handler = PostgresAuditHandler()
outbox = AuditOutbox(Path(settings.audit_outbox_path), settings.audit_outbox_max_bytes,
                     settings.audit_outbox_max_events)


def _deliver(body):
    db_handler.emit(logging.makeLogRecord({"msg": "[AUDIT] " + body}))


worker = AuditDeliveryWorker(outbox, _deliver, diagnostics.report)
log_queue = queue.Queue(maxsize=settings.audit_queue_size)
queue_handler = BoundedAuditQueueHandler(log_queue, outbox, worker, (file_handler, stream_handler), diagnostics)
logger.addHandler(queue_handler)
listener = BoundedAuditQueueListener(log_queue, file_handler, stream_handler, respect_handler_level=True)
listener.start()
worker.start()


def _shutdown():
    if not listener.stop():
        diagnostics.report("file_shutdown_pending")
    if worker.stop():
        outbox.close()
    else:
        # Claimed records remain on disk and become eligible when their lease expires.
        diagnostics.report("db_shutdown_pending")


atexit.register(_shutdown)


def audit_status():
    events, size = outbox.usage()
    return {"file_queue_size": log_queue.qsize(), "file_queue_limit": log_queue.maxsize,
            "pending_db_events": events, "pending_db_bytes": size,
            "outbox_event_limit": outbox.max_events, "outbox_byte_limit": outbox.max_bytes,
            "counters": diagnostics.snapshot()}


def log_audit(action: str, status: str, user_id: str = "SYSTEM", payload: Dict[str, Any] = None):
    safe_payload = sanitize_dict(payload or {})
    data = {
        "event_id": str(uuid.uuid4()),
        "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "level": "AUDIT",
        "db_delivery": "outbox_pending",
        "user_id": sanitize_text(user_id)[:16] if user_id else "SYSTEM",
        "action": sanitize_text(action)[:64],
        "status": sanitize_text(status)[:32],
        "payload": safe_payload,
    }
    body = json.dumps(data, ensure_ascii=False, default=lambda _: "<unsupported-value>")
    encoded = body.encode("utf-8")
    if len(encoded) > settings.audit_record_max_bytes:
        data["payload"] = {"_audit_payload_omitted": True, "original_record_bytes": len(encoded),
                           "sha256": hashlib.sha256(encoded).hexdigest()}
        body = json.dumps(data, ensure_ascii=False)
        diagnostics.report("oversized_payload_summarized")
    logger.info("[AUDIT] %s", body)
