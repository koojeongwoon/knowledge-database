import concurrent.futures
import threading
import time
from unittest.mock import MagicMock, patch

import pytest
from psycopg_pool import PoolTimeout

from src.api.exceptions import DatabaseException
from src.core.database import connection


@pytest.mark.parametrize("failure", [PoolTimeout("busy"), RuntimeError("unavailable")])
def test_pool_failure_returns_unavailable_without_opening_a_direct_connection(failure):
    pool = MagicMock()
    pool.getconn.side_effect = failure
    manager = connection.PostgresDatabaseManager()
    with patch.object(connection, "get_connection_pool", return_value=pool), \
         patch("psycopg.connect") as direct:
        with pytest.raises(DatabaseException) as error:
            manager.connect()
    assert error.value.status_code == 503
    assert manager.conn is None
    direct.assert_not_called()


def test_closed_connection_is_returned_to_pool_so_its_slot_can_be_replaced():
    pool = MagicMock()
    manager = connection.PostgresDatabaseManager()
    manager.conn = MagicMock(closed=True)
    borrowed = manager.conn
    manager._from_pool = True
    with patch.object(connection, "get_connection_pool", return_value=pool):
        manager.close()
    pool.putconn.assert_called_once_with(borrowed)
    assert manager.conn is None


def test_initialization_failure_discards_and_returns_the_borrowed_connection():
    pool = MagicMock()
    borrowed = MagicMock(closed=False)
    pool.getconn.return_value = borrowed
    manager = connection.PostgresDatabaseManager()
    with patch.object(connection, "get_connection_pool", return_value=pool), \
         patch.object(connection, "_configure_connection", side_effect=RuntimeError("bad connection")), \
         patch("psycopg.connect") as direct:
        with pytest.raises(DatabaseException):
            manager.connect()
    borrowed.close.assert_called_once()
    pool.putconn.assert_called_once_with(borrowed)
    assert manager.conn is None
    direct.assert_not_called()


def test_concurrent_first_requests_share_one_pool(monkeypatch):
    monkeypatch.setattr(connection, "_connection_pool", None)
    barrier = threading.Barrier(4)
    pool = MagicMock()

    def construct(**kwargs):
        time.sleep(0.03)
        return pool

    def request(_):
        barrier.wait()
        return connection.get_connection_pool()

    with patch.object(connection, "ConnectionPool", side_effect=construct) as factory:
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as executor:
            assert list(executor.map(request, range(4))) == [pool] * 4
    factory.assert_called_once()
