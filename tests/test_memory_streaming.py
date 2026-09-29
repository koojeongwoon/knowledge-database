import os
import unittest
from unittest.mock import MagicMock

import psycopg

from src.api.exceptions import DatabaseException
from src.core.config import current_user_config
from src.core.database.connection import PostgresDatabaseManager
from src.indexing.infrastructure.indexing_repository import PostgresIndexingRepository


class StreamingCursorTests(unittest.TestCase):
    def setUp(self):
        self.manager = PostgresDatabaseManager()
        self.manager.connect = MagicMock()
        self.manager.close = MagicMock()
        self.manager.conn = MagicMock()
        self.cursor = self.manager.conn.cursor.return_value.__enter__.return_value

    def test_named_cursor_is_closed_before_transaction_and_connection(self):
        events = []
        self.manager.conn.cursor.return_value.__exit__.side_effect = lambda *_: events.append("cursor")
        self.manager.conn.transaction.return_value.__exit__.side_effect = lambda *_: events.append("transaction")
        self.manager.close.side_effect = lambda: events.append("connection")
        with self.manager.streaming_cursor(100) as cur:
            self.assertIs(cur, self.cursor)
            self.assertEqual(cur.itersize, 100)
        self.assertTrue(self.manager.conn.cursor.call_args.kwargs["name"].startswith("knowledge_read_"))
        self.assertEqual(events, ["cursor", "transaction", "connection"])

    def test_query_failure_rolls_back_and_returns_connection(self):
        failure = RuntimeError("query failed")
        with self.assertRaises(DatabaseException):
            with self.manager.streaming_cursor():
                raise failure
        self.manager.conn.cursor.return_value.__exit__.assert_called_once()
        self.manager.conn.transaction.return_value.__exit__.assert_called_once()
        self.assertIs(self.manager.conn.transaction.return_value.__exit__.call_args.args[1], failure)
        self.manager.close.assert_called_once()

    def test_invalid_batch_size_does_not_borrow_connection(self):
        with self.assertRaises(ValueError):
            with self.manager.streaming_cursor(0):
                pass
        self.manager.connect.assert_not_called()

    def test_chunk_iterator_closes_cursor_when_consumer_stops(self):
        self.cursor.__iter__.return_value = iter([(0, "first", "[0.1]"), (1, "second", "[0.2]")])
        chunks = PostgresIndexingRepository(self.manager).iter_document_chunks("qa/a.md")
        self.assertEqual(next(chunks)["content"], "first")
        self.manager.close.assert_not_called()
        chunks.close()
        self.manager.close.assert_called_once()
        self.cursor.fetchall.assert_not_called()


TEST_DATABASE_URL = os.getenv("KNOWLEDGE_MEMORY_TEST_DATABASE_URL")


class IsolatedDatabaseManager(PostgresDatabaseManager):
    def connect(self):
        if self.conn is not None:
            return
        self.conn = psycopg.connect(TEST_DATABASE_URL, autocommit=True)
        # Session-local fixtures never modify persistent application tables.
        self.conn.execute("""
            CREATE TEMP TABLE knowledge_documents (
                owner_id text, file_path text, content_hash text,
                chunk_index integer, content text, embedding text
            )
        """)
        self.conn.execute("""
            INSERT INTO knowledge_documents
            SELECT 'memory-test', 'qa/a.md', 'hash-a', n,
                   'chunk-' || n, '[0.1,0.2]'
            FROM generate_series(0, 1199) AS n
        """)
        self.conn.execute("""
            INSERT INTO knowledge_documents
            VALUES ('another-owner', 'qa/other.md', 'other-hash', 0, 'private', '[0.9]')
        """)


@unittest.skipUnless(TEST_DATABASE_URL, "KNOWLEDGE_MEMORY_TEST_DATABASE_URL is not configured")
class PostgresStreamingIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.token = current_user_config.set({"user_id": "memory-test"})
        self.manager = IsolatedDatabaseManager()
        self.repo = PostgresIndexingRepository(self.manager)

    def tearDown(self):
        self.manager.close()
        current_user_config.reset(self.token)

    def test_hash_queries_preserve_owner_scope_and_filters(self):
        self.assertEqual(self.repo.get_all_file_hashes(), {"qa/a.md": "hash-a"})
        self.assertIsNone(self.manager.conn)
        self.assertEqual(self.repo.get_file_hashes(["qa/a.md", "qa/other.md"]), {"qa/a.md": "hash-a"})
        self.assertEqual(self.repo.get_file_hashes(["qa/missing.md"]), {})
        self.assertEqual(self.repo.get_file_hashes([]), {})

    def test_chunk_reads_cross_batches_and_preserve_list_contract(self):
        chunks = self.repo.get_document_chunks("qa/a.md")
        self.assertIsInstance(chunks, list)
        self.assertEqual(len(chunks), 1200)
        self.assertEqual([item["chunk_index"] for item in chunks], list(range(1200)))
        self.assertEqual(chunks[-1]["embedding"], [0.1, 0.2])
        self.assertIsNone(self.manager.conn)

    def test_early_close_and_query_error_release_real_connection(self):
        chunks = self.repo.iter_document_chunks("qa/a.md")
        next(chunks)
        conn = self.manager.conn
        chunks.close()
        self.assertTrue(conn.closed)
        self.assertIsNone(self.manager.conn)
        with self.assertRaises(DatabaseException):
            with self.manager.streaming_cursor() as cur:
                conn = self.manager.conn
                cur.execute("SELECT missing_column FROM knowledge_documents")
        self.assertTrue(conn.closed)
        self.assertIsNone(self.manager.conn)
