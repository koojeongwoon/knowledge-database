import json
import logging
import os
import time
import uuid
from unittest.mock import Mock, patch

import psycopg
import pytest
from psycopg import sql

from src.core.logging.audit import PostgresAuditHandler
from src.core.logging.outbox import AuditDeliveryWorker, AuditOutbox


@pytest.fixture
def isolated_audit_database():
    url = os.getenv("AUDIT_TEST_DATABASE_URL")
    if not url:
        pytest.skip("AUDIT_TEST_DATABASE_URL is not configured")
    schema = "audit_test_" + uuid.uuid4().hex
    conn = psycopg.connect(url, autocommit=True)
    conn.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))

    def connect(_url, **kwargs):
        connection = psycopg.connect(url, **kwargs)
        connection.execute(sql.SQL("SET search_path TO {}").format(sql.Identifier(schema)))
        return connection

    try:
        yield conn, schema, PostgresAuditHandler(connect=connect)
    finally:
        conn.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))
        conn.close()


def test_real_postgres_failure_recovery_and_commit_acknowledgement(tmp_path, isolated_audit_database):
    conn, schema, handler = isolated_audit_database
    event_id = str(uuid.uuid4())
    data = {"event_id": event_id, "timestamp": "2026-09-29T00:00:00Z", "user_id": "USER_1",
            "action": "TEST_RECOVERY", "status": "SUCCESS", "payload": {"info": "safe"}}
    outbox = AuditOutbox(tmp_path / "audit.sqlite3", 10000, 10)
    report = Mock()

    def deliver(body):
        handler.emit(logging.makeLogRecord({"msg": "[AUDIT] " + body}))

    try:
        outbox.put(event_id, json.dumps(data))
        worker = AuditDeliveryWorker(outbox, deliver, report)
        # The table is absent: a real DB error must leave the record pending.
        assert worker.run_once()
        assert outbox.usage()[0] == 1
        report.assert_called_once_with("db_delivery_failed")
        outbox.close()
        resumed = AuditOutbox(tmp_path / "audit.sqlite3", 10000, 10)
        outbox = resumed
        conn.execute(sql.SQL("""
            CREATE TABLE {}.knowledge_audit_logs (
                user_id text, action text, status text, payload jsonb
            )
        """).format(sql.Identifier(schema)))
        recovered = AuditDeliveryWorker(resumed, deliver, report)
        with patch("src.core.logging.outbox.time.time", return_value=time.time() + 120):
            assert recovered.run_once()
        assert resumed.usage() == (0, 0)
        row = conn.execute(sql.SQL("SELECT user_id, action, payload FROM {}.knowledge_audit_logs")
                           .format(sql.Identifier(schema))).fetchone()
        assert row[:2] == ("USER_1", "TEST_RECOVERY")
        assert row[2]["_audit_delivery"] == {"event_id": event_id, "recorded_at": data["timestamp"]}
        assert not recovered.run_once()
    finally:
        outbox.close()
