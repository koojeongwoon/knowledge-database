import asyncio
import json
import logging
import queue
import time
from threading import Event
from unittest.mock import Mock, patch

from src.core.logging import audit
from src.core.logging.outbox import AuditDeliveryWorker, AuditOutbox


def body(event_id):
    return json.dumps({"event_id": event_id, "timestamp": "2026-09-29T00:00:00Z",
                       "user_id": "USER_1", "action": "TEST", "status": "SUCCESS",
                       "payload": {"info": "safe"}})


def record(event_id):
    return logging.makeLogRecord({"msg": "[AUDIT] " + body(event_id), "levelno": logging.INFO})


def test_outbox_capacity_and_usage_survive_restart(tmp_path):
    path = tmp_path / "audit.sqlite3"
    outbox = AuditOutbox(path, 100, 2)
    assert outbox.put("a", "x" * 60)
    assert not outbox.put("b", "x" * 41)
    assert outbox.put("c", "x" * 30)
    assert not outbox.put("d", "x")
    assert outbox.usage() == (2, 90)
    outbox.close()
    resumed = AuditOutbox(path, 100, 2)
    try:
        assert resumed.usage() == (2, 90)
        assert resumed.claim("worker")[0] == "a"
        resumed.acknowledge("a", "worker")
        assert resumed.usage() == (1, 30)
        assert resumed.put("new", "x" * 70)
    finally:
        resumed.close()


def test_shared_outbox_claims_once_and_recovers_expired_lease(tmp_path):
    path = tmp_path / "audit.sqlite3"
    first, second = AuditOutbox(path, 1000, 2), AuditOutbox(path, 1000, 2)
    try:
        assert first.put("a", body("a"))
        with patch("src.core.logging.outbox.time.time", return_value=100):
            assert first.claim("worker-1", lease_seconds=2)[0] == "a"
            assert second.claim("worker-2") is None
        first.close()
        with patch("src.core.logging.outbox.time.time", return_value=103):
            assert second.claim("worker-2")[0] == "a"
            second.acknowledge("a", "worker-1")
            assert second.usage()[0] == 1
            second.acknowledge("a", "worker-2")
            assert second.usage() == (0, 0)
    finally:
        first.close()
        second.close()


def test_failed_delivery_is_retried_after_backoff(tmp_path):
    outbox = AuditOutbox(tmp_path / "audit.sqlite3", 1000, 2)
    deliver, report = Mock(side_effect=[RuntimeError("sensitive-error"), None]), Mock()
    worker = AuditDeliveryWorker(outbox, deliver, report)
    try:
        outbox.put("a", body("a"))
        with patch("src.core.logging.outbox.time.time", return_value=100):
            assert worker.run_once()
            assert outbox.usage()[0] == 1
            assert not worker.run_once()
        with patch("src.core.logging.outbox.time.time", return_value=102):
            assert worker.run_once()
            assert outbox.usage() == (0, 0)
        report.assert_called_once_with("db_delivery_failed")
        assert deliver.call_count == 2
    finally:
        outbox.close()


def test_full_file_queue_writes_synchronously_without_dropping_record(tmp_path):
    outbox = AuditOutbox(tmp_path / "audit.sqlite3", 1000, 4)
    local = logging.FileHandler(tmp_path / "audit.log")
    q = queue.Queue(maxsize=1)
    diagnostics = audit.AuditDiagnostics()
    worker = Mock()
    handler = audit.BoundedAuditQueueHandler(q, outbox, worker, (local,), diagnostics)
    try:
        handler.handle(record("first"))
        handler.handle(record("second"))
        assert q.qsize() == 1
        assert '"event_id": "second"' in (tmp_path / "audit.log").read_text()
        assert outbox.usage()[0] == 2
        assert diagnostics.snapshot()["file_queue_full_sync_write"] == 1
        assert worker.wake.call_count == 2
    finally:
        local.close()
        outbox.close()


def test_outbox_full_is_reported_while_file_record_remains(tmp_path):
    outbox = AuditOutbox(tmp_path / "audit.sqlite3", 1000, 1)
    local = logging.FileHandler(tmp_path / "audit.log")
    q = queue.Queue(maxsize=1)
    diagnostics = audit.AuditDiagnostics()
    handler = audit.BoundedAuditQueueHandler(q, outbox, Mock(), (local,), diagnostics)
    try:
        handler.handle(record("first"))
        handler.handle(record("second"))
        assert outbox.usage()[0] == 1
        assert diagnostics.snapshot()["outbox_full_file_only"] == 1
        assert '"event_id": "second"' in (tmp_path / "audit.log").read_text()
        assert '"db_delivery": "file_only"' in (tmp_path / "audit.log").read_text()
    finally:
        local.close()
        outbox.close()


def test_outbox_io_failure_does_not_prevent_local_file_write(tmp_path, caplog):
    outbox = Mock()
    outbox.put.side_effect = OSError("secret-connection-detail")
    local = logging.FileHandler(tmp_path / "audit.log")
    q = queue.Queue(maxsize=1)
    diagnostics = audit.AuditDiagnostics()
    handler = audit.BoundedAuditQueueHandler(q, outbox, Mock(), (local,), diagnostics)
    try:
        q.put(record("first"))
        handler.handle(record("second"))
        assert diagnostics.snapshot()["outbox_write_failed_file_only"] == 1
        assert '"event_id": "second"' in (tmp_path / "audit.log").read_text()
        assert "secret-connection-detail" not in caplog.text
    finally:
        local.close()


def test_blocked_db_delivery_does_not_delay_file_writes(tmp_path):
    outbox = AuditOutbox(tmp_path / "audit.sqlite3", 10000, 20)
    entered, release, files_written = Event(), Event(), Event()

    def deliver(_):
        entered.set()
        if not release.wait(5):
            raise TimeoutError("test delivery was not released")

    local = logging.FileHandler(tmp_path / "audit.log")
    sink = Mock()
    sink.handle.side_effect = lambda _: files_written.set()
    q = queue.Queue(maxsize=10)
    diagnostics = audit.AuditDiagnostics()
    worker = AuditDeliveryWorker(outbox, deliver, diagnostics.report)
    handler = audit.BoundedAuditQueueHandler(q, outbox, worker, (local,), diagnostics)
    listener = audit.BoundedAuditQueueListener(q, local, sink)
    listener.start()
    worker.start()
    try:
        handler.handle(record("first"))
        assert entered.wait(2)
        files_written.clear()
        handler.handle(record("second"))
        assert files_written.wait(2)
        # The listener signals only after the preceding file handler completes.
        assert '"event_id": "second"' in (tmp_path / "audit.log").read_text()
        assert outbox.usage()[0] == 2
    finally:
        release.set()
        assert listener.stop()
        assert worker.stop()
        local.close()
        outbox.close()


def test_listener_shutdown_has_deadline_when_queue_is_full():
    entered, release = Event(), Event()
    sink = Mock()

    def blocked(_):
        entered.set()
        release.wait(5)

    sink.handle.side_effect = blocked
    q = queue.Queue(maxsize=1)
    listener = audit.BoundedAuditQueueListener(q, sink)
    listener.start()
    try:
        q.put(record("first"))
        assert entered.wait(2)
        q.put(record("second"))
        start = time.monotonic()
        assert not listener.stop(timeout=0.01)
        assert time.monotonic() - start < 1
    finally:
        release.set()
        assert listener.stop()


def test_db_shutdown_has_deadline_and_preserves_unconfirmed_record(tmp_path):
    outbox = AuditOutbox(tmp_path / "audit.sqlite3", 1000, 2)
    entered, release = Event(), Event()

    def deliver(_):
        entered.set()
        release.wait(5)
        raise RuntimeError("delivery remains unconfirmed")

    outbox.put("a", body("a"))
    worker = AuditDeliveryWorker(outbox, deliver, Mock())
    worker.start()
    try:
        assert entered.wait(2)
        start = time.monotonic()
        assert not worker.stop(timeout=0.01)
        assert time.monotonic() - start < 1
        assert outbox.usage()[0] == 1
    finally:
        release.set()
        assert worker.stop()
        outbox.close()
    resumed = AuditOutbox(tmp_path / "audit.sqlite3", 1000, 2)
    try:
        assert resumed.usage()[0] == 1
    finally:
        resumed.close()


def test_record_byte_limit_and_redaction_are_applied_before_queueing():
    with patch.object(audit.settings, "audit_record_max_bytes", 1024):
        with patch.object(audit.logger, "info") as info:
            audit.log_audit("한" * 200, "성" * 100, payload={"secret_key": "sensitive", "text": "가" * 10000})
    serialized = info.call_args.args[1]
    assert len(serialized.encode("utf-8")) <= 1024
    assert json.loads(serialized)["payload"]["_audit_payload_omitted"] is True
    assert "sensitive" not in serialized


def test_postgres_delivery_preserves_event_id_and_uses_timeouts():
    with patch("src.core.logging.audit.psycopg.connect") as connect:
        audit.PostgresAuditHandler().emit(record("a"))
    assert connect.call_args.kwargs["connect_timeout"] == 2
    assert "statement_timeout=3000" in connect.call_args.kwargs["options"]
    cur = connect.return_value.__enter__.return_value.cursor.return_value.__enter__.return_value
    params = cur.execute.call_args.args[1]
    assert params[:3] == ("USER_1", "TEST", "SUCCESS")
    assert json.loads(params[3])["_audit_delivery"]["event_id"] == "a"


def test_file_error_is_reported_without_dumping_record(tmp_path):
    handler = audit.AuditFileHandler(tmp_path / "audit.log")
    try:
        with patch.object(handler.stream, "write", side_effect=OSError("sensitive-disk-detail")):
            with patch.object(audit.diagnostics, "report") as report:
                handler.handle(record("a"))
        report.assert_called_once_with("file_write_failed")
    finally:
        handler.close()


def test_failed_authentication_does_not_use_raw_api_key_as_audit_identity():
    from src.api.middleware import _validate_api_key_cached

    with patch("src.api.middleware.cache_manager") as cache:
        cache.get.return_value = None
        with patch("src.api.middleware._validate_api_key_from_db", return_value=None):
            with patch("src.api.middleware.log_audit") as log:
                assert asyncio.run(_validate_api_key_cached("opaque-secret-api-key")) is None
    assert log.call_args.kwargs["user_id"] == "UNKNOWN"
    assert "opaque-secret-api-key" not in str(log.call_args)
