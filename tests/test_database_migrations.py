import unittest
from contextlib import contextmanager

from src.core.database.migrations import MIGRATIONS, run_database_migrations


class FakeCursor:
    def __init__(self, applied_versions):
        self.applied_versions = applied_versions
        self.statements = []
        self.params = []

    def execute(self, statement, params=None):
        normalized = " ".join(statement.split())
        self.statements.append(normalized)
        self.params.append(params)
        if normalized.startswith("INSERT INTO knowledge_schema_migrations"):
            self.applied_versions.add(params[0])

    def fetchall(self):
        if self.statements[-1].startswith("SELECT version FROM knowledge_schema_migrations"):
            return [(version,) for version in sorted(self.applied_versions)]
        return []


class FakeDatabaseManager:
    def __init__(self):
        self.applied_versions = set()
        self.cursors = []
        self.transaction_count = 0

    @contextmanager
    def transaction(self):
        self.transaction_count += 1
        cursor = FakeCursor(self.applied_versions)
        self.cursors.append(cursor)
        yield cursor


class DatabaseMigrationTests(unittest.TestCase):
    def test_all_schema_components_are_applied_and_recorded(self):
        manager = FakeDatabaseManager()

        applied = run_database_migrations(manager)

        self.assertEqual(applied, [migration.version for migration in MIGRATIONS])
        sql = "\n".join(manager.cursors[0].statements)
        self.assertIn("CREATE TABLE IF NOT EXISTS knowledge_documents", sql)
        self.assertIn("CREATE TABLE IF NOT EXISTS knowledge_indexing_jobs", sql)
        self.assertIn("CREATE TABLE IF NOT EXISTS knowledge_user_settings", sql)
        self.assertIn("CREATE TABLE IF NOT EXISTS knowledge_search_events", sql)
        self.assertIn("CREATE TABLE IF NOT EXISTS knowledge_search_feedback", sql)
        self.assertIn("ADD COLUMN IF NOT EXISTS partially_relevant_paths", sql)
        self.assertIn("ADD COLUMN IF NOT EXISTS satisfaction", sql)
        self.assertIn("CREATE TABLE IF NOT EXISTS knowledge_schema_migrations", sql)
        self.assertIn("SELECT pg_advisory_xact_lock", sql)

    def test_already_applied_migrations_are_not_repeated(self):
        manager = FakeDatabaseManager()
        run_database_migrations(manager)

        applied = run_database_migrations(manager)

        self.assertEqual(applied, [])
        second_sql = "\n".join(manager.cursors[1].statements)
        self.assertNotIn("CREATE TABLE IF NOT EXISTS knowledge_documents", second_sql)
        self.assertNotIn("CREATE TABLE IF NOT EXISTS knowledge_user_settings", second_sql)

    def test_duplicate_migration_versions_are_rejected(self):
        manager = FakeDatabaseManager()
        duplicate = (MIGRATIONS[0], MIGRATIONS[0])

        with self.assertRaisesRegex(ValueError, "중복"):
            run_database_migrations(manager, duplicate)


if __name__ == "__main__":
    unittest.main()
