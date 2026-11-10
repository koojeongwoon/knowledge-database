"""Opt-in checks against a disposable loopback-only PostgreSQL fixture."""
import os
from urllib.parse import urlparse

import psycopg
import pytest
from psycopg_pool import ConnectionPool

from src.api.exceptions import DatabaseException
from src.core.database import connection


def test_exhaustion_and_query_cancellation_preserve_the_connection_limit(monkeypatch):
    dsn = os.getenv("KNOWLEDGE_POOL_TEST_DSN")
    if not dsn:
        pytest.skip("KNOWLEDGE_POOL_TEST_DSN is not configured")
    parsed = urlparse(dsn)
    assert parsed.hostname == "127.0.0.1" and parsed.path == "/db_pool_test"
    pool = ConnectionPool(
        dsn, min_size=0, max_size=1, timeout=0.15, max_waiting=1, open=True,
        kwargs={"autocommit": True, "options": "-c statement_timeout=100 -c lock_timeout=50"},
        check=ConnectionPool.check_connection,
    )
    monkeypatch.setattr(connection, "_connection_pool", pool)
    monkeypatch.setattr(connection, "_configure_connection", lambda _: None)
    try:
        holder = connection.PostgresDatabaseManager()
        holder.connect()
        with pytest.raises(DatabaseException):
            connection.PostgresDatabaseManager().connect()
        assert pool.get_stats()["pool_size"] == 1
        holder.close()
        manager = connection.PostgresDatabaseManager()
        with pytest.raises(DatabaseException) as failure:
            with manager.cursor() as cursor:
                cursor.execute("SELECT pg_sleep(1)")
        assert isinstance(failure.value.__cause__, psycopg.errors.QueryCanceled)
        with manager.cursor() as cursor:
            cursor.execute("SELECT 1")
            assert cursor.fetchone() == (1,)
        assert pool.get_stats()["pool_size"] == 1
    finally:
        pool.close()
