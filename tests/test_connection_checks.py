from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from psycopg import OperationalError
from psycopg.pq import ConnStatus, TransactionStatus

from src.core.database import connection


def setup_check(monkeypatch):
    clock = [0.0]
    monkeypatch.setattr(connection, "monotonic", lambda: clock[0], raising=False)
    monkeypatch.setattr(connection, "settings", SimpleNamespace(db_pool_check_interval_seconds=1.0))
    probe = Mock()
    monkeypatch.setattr(connection.ConnectionPool, "check_connection", probe)
    conn = SimpleNamespace(closed=False, pgconn=SimpleNamespace(
        status=ConnStatus.OK, transaction_status=TransactionStatus.IDLE,
        consume_input=Mock(), is_busy=Mock(return_value=False),get_result=Mock(return_value=None)))
    return conn, clock, probe


def test_hot_borrows_check_protocol_each_time_but_probe_once(monkeypatch):
    conn, clock, probe = setup_check(monkeypatch)
    for clock[0] in [0.0, 0.25, 0.99]:
        connection._check_connection(conn)
    assert probe.call_count == 1
    assert conn.pgconn.consume_input.call_count == 3
    assert conn.pgconn.is_busy.call_count == 3


def test_elapsed_interval_and_disabled_interval_retain_network_probes(monkeypatch):
    conn, clock, probe = setup_check(monkeypatch)
    connection._check_connection(conn)
    clock[0] = 1.0
    connection._check_connection(conn)
    assert probe.call_count == 2
    monkeypatch.setattr(connection.settings, "db_pool_check_interval_seconds", 0)
    connection._check_connection(conn)
    assert probe.call_count == 3


def test_protocol_failure_is_not_hidden_by_recent_probe(monkeypatch):
    conn, clock, probe = setup_check(monkeypatch)
    connection._check_connection(conn)
    conn.pgconn.consume_input.side_effect = OperationalError("disconnected")
    clock[0] = 0.1
    with pytest.raises(OperationalError):
        connection._check_connection(conn)
    assert probe.call_count == 1


@pytest.mark.parametrize("state", ["closed", "bad", "busy", "active", "pending_result"])
def test_unusable_connection_is_rejected_during_hot_reuse(monkeypatch, state):
    conn, clock, probe = setup_check(monkeypatch)
    connection._check_connection(conn)
    if state == "closed":
        conn.closed = True
    elif state == "bad":
        conn.pgconn.status = ConnStatus.BAD
    elif state == "busy":
        conn.pgconn.is_busy.return_value = True
    elif state == "active":
        conn.pgconn.transaction_status = TransactionStatus.ACTIVE
    else:
        conn.pgconn.get_result.return_value = object()
    with pytest.raises(OperationalError):
        connection._check_connection(conn)
    assert probe.call_count == 1


def test_failed_network_probe_does_not_mark_connection_checked(monkeypatch):
    conn, clock, probe = setup_check(monkeypatch)
    probe.side_effect = [OperationalError("probe failed"), None]
    with pytest.raises(OperationalError):
        connection._check_connection(conn)
    clock[0] = 0.1
    connection._check_connection(conn)
    assert probe.call_count == 2
