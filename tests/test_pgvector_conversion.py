import unittest
from contextlib import contextmanager

from pgvector import Vector

from src.core.config import current_user_config
from src.indexing.infrastructure.indexing_repository import PostgresIndexingRepository


class _Cursor:
    def execute(self, query, params):
        pass

    def fetchall(self):
        return [(0, "content", Vector([0.1, 0.2, 0.3]))]


class _DatabaseManager:
    @contextmanager
    def cursor(self):
        yield _Cursor()


class PgvectorConversionTests(unittest.TestCase):
    def test_document_chunks_convert_pgvector_vector_to_list(self):
        token = current_user_config.set({"user_id": "USER_1"})
        try:
            chunks = PostgresIndexingRepository(_DatabaseManager()).get_document_chunks("qa/note.md")
        finally:
            current_user_config.reset(token)

        self.assertEqual(len(chunks[0]["embedding"]), 3)
        for actual, expected in zip(chunks[0]["embedding"], [0.1, 0.2, 0.3]):
            self.assertAlmostEqual(actual, expected, places=6)


if __name__ == "__main__":
    unittest.main()
