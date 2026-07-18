import os
import json
import unittest
import uuid
from contextlib import contextmanager

import psycopg

from src.core.config import current_user_config
from src.core.database.migrations import run_database_migrations
from src.ontology.domain.model import Concept, DocumentConcept, OntologySnapshot, Relation
from src.ontology.repository import PostgresOntologyRepository
from src.ontology.service import OntologyShadowOutcome
from src.retrieval.feedback import SearchFeedbackService


TEST_DATABASE_URL = os.getenv("ONTOLOGY_TEST_DATABASE_URL")


class IntegrationDatabaseManager:
    def __init__(self, url: str):
        self.url = url

    @contextmanager
    def cursor(self):
        with psycopg.connect(self.url, autocommit=True) as conn:
            with conn.cursor() as cur:
                yield cur

    @contextmanager
    def transaction(self):
        with psycopg.connect(self.url) as conn:
            with conn.transaction():
                with conn.cursor() as cur:
                    yield cur


@unittest.skipUnless(TEST_DATABASE_URL, "ONTOLOGY_TEST_DATABASE_URL is not configured")
class OntologyPostgresIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.manager = IntegrationDatabaseManager(TEST_DATABASE_URL)
        run_database_migrations(cls.manager)

    def setUp(self):
        with self.manager.cursor() as cur:
            cur.execute("TRUNCATE knowledge_ontology_shadow_events RESTART IDENTITY CASCADE;")
            cur.execute("TRUNCATE knowledge_ontology_concepts CASCADE;")
        self.owner_token = current_user_config.set({"user_id": "phase3-owner"})
        self.repository = PostgresOntologyRepository(self.manager)

    def tearDown(self):
        current_user_config.reset(self.owner_token)

    @staticmethod
    def snapshot(source_path: str, *concept_ids: str) -> OntologySnapshot:
        concepts = tuple(Concept(value, value.title()) for value in concept_ids)
        links = tuple(DocumentConcept(source_path, value) for value in concept_ids)
        relations = (
            (Relation(concept_ids[0], "uses", concept_ids[1]),)
            if len(concept_ids) > 1 else ()
        )
        return OntologySnapshot(source_path, concepts, relations, links)

    def counts(self):
        with self.manager.cursor() as cur:
            cur.execute("""
                SELECT
                    (SELECT count(*) FROM knowledge_ontology_concepts),
                    (SELECT count(*) FROM knowledge_ontology_concept_sources),
                    (SELECT count(*) FROM knowledge_ontology_relations),
                    (SELECT count(*) FROM knowledge_document_concepts);
            """)
            return cur.fetchone()

    def test_migrations_14_through_16_are_applied_and_idempotent(self):
        self.assertEqual(run_database_migrations(self.manager), [])
        with self.manager.cursor() as cur:
            cur.execute("SELECT version FROM knowledge_schema_migrations WHERE version IN (14, 15, 16) ORDER BY version;")
            self.assertEqual(cur.fetchall(), [(14,), (15,), (16,)])

    def test_same_snapshot_is_idempotent(self):
        snapshot = self.snapshot("qa/a.md", "service", "database")
        self.repository.replace_explicit_snapshot(snapshot)
        first = self.counts()
        self.repository.replace_explicit_snapshot(snapshot)
        second = self.counts()
        self.assertEqual(first, (2, 2, 1, 2))
        self.assertEqual(second, first)
        with self.manager.cursor() as cur:
            cur.execute("SELECT count(*) FROM knowledge_ontology_relation_evidence;")
            self.assertEqual(cur.fetchone(), (1,))

    def test_failed_replacement_rolls_back_and_preserves_previous_snapshot(self):
        original = self.snapshot("qa/a.md", "service", "database")
        self.repository.replace_explicit_snapshot(original)
        before = self.counts()
        invalid_metadata = OntologySnapshot(
            "qa/a.md",
            original.concepts,
            (Relation("service", "uses", "database", metadata={"bad": {"set"}}),),
            original.document_concepts,
        )
        with self.assertRaises(TypeError):
            self.repository.replace_explicit_snapshot(invalid_metadata)
        self.assertEqual(self.counts(), before)

    def test_shared_concept_provenance_survives_one_source_deletion(self):
        self.repository.replace_explicit_snapshot(self.snapshot("qa/a.md", "shared", "only-a"))
        self.repository.replace_explicit_snapshot(self.snapshot("qa/b.md", "shared", "only-b"))
        self.repository.delete_explicit_source("qa/a.md")
        with self.manager.cursor() as cur:
            cur.execute("SELECT concept_id FROM knowledge_ontology_concepts ORDER BY concept_id;")
            self.assertEqual(cur.fetchall(), [("only-b",), ("shared",)])
            cur.execute("SELECT source_path FROM knowledge_ontology_concept_sources WHERE concept_id = 'shared';")
            self.assertEqual(cur.fetchall(), [("qa/b.md",)])

    def test_owner_isolation_and_shadow_event_persistence(self):
        self.repository.replace_explicit_snapshot(self.snapshot("qa/a.md", "shared"))
        other_token = current_user_config.set({"user_id": "other-owner"})
        try:
            other_repository = PostgresOntologyRepository(self.manager)
            other_repository.replace_explicit_snapshot(self.snapshot("qa/a.md", "shared"))
            other_repository.record_shadow_event(
                "qa/a.md",
                OntologyShadowOutcome(True, True, status="persisted", concept_count=1),
            )
        finally:
            current_user_config.reset(other_token)
        with self.manager.cursor() as cur:
            cur.execute("SELECT owner_id, count(*) FROM knowledge_ontology_concepts GROUP BY owner_id ORDER BY owner_id;")
            self.assertEqual(cur.fetchall(), [("other-owner", 1), ("phase3-owner", 1)])
            cur.execute("SELECT owner_id, status FROM knowledge_ontology_shadow_events;")
            self.assertEqual(cur.fetchall(), [("other-owner", "persisted")])

    def test_ontology_feedback_fields_round_trip(self):
        search_id = str(uuid.uuid4())
        returned = [{"file_path": "qa/a.md", "rank": 1}]
        with self.manager.cursor() as cur:
            cur.execute("""
                INSERT INTO knowledge_search_events (
                    search_id, owner_id, query_text, query_hash, returned_results,
                    result_count, pipeline_version
                ) VALUES (%s, %s, 'query', %s, %s::jsonb, 1, 'test');
            """, (search_id, "phase3-owner", "0" * 64, json.dumps(returned)))
        SearchFeedbackService(self.manager).submit(
            "phase3-owner", search_id, ["qa/a.md"], [], False,
            expected_relations=[{"subject": "service", "predicate": "depends_on", "object": "database"}],
            expected_graph_paths=[["service", "database"]],
            forbidden_paths=["qa/old.md"],
            expected_rule_types=["prefer_current"],
            ontology_notes="expected dependency",
            result_feedback=[{
                "file_path": "qa/a.md", "relevance_grade": 3,
                "ontology_context_grade": 2, "relation_path_correct": True,
                "rule_application_correct": False,
            }],
        )
        with self.manager.cursor() as cur:
            cur.execute("""
                SELECT expected_relations, expected_graph_paths, forbidden_paths,
                       expected_rule_types, ontology_notes
                FROM knowledge_search_feedback WHERE search_id = %s;
            """, (search_id,))
            feedback = cur.fetchone()
            self.assertEqual(feedback[0][0]["predicate"], "depends_on")
            self.assertEqual(feedback[1], [["service", "database"]])
            self.assertEqual(feedback[2], ["qa/old.md"])
            self.assertEqual(feedback[3], ["prefer_current"])
            self.assertEqual(feedback[4], "expected dependency")
            cur.execute("""
                SELECT ontology_context_grade, relation_path_correct,
                       rule_application_correct
                FROM knowledge_search_result_feedback WHERE search_id = %s;
            """, (search_id,))
            self.assertEqual(cur.fetchone(), (2, True, False))


if __name__ == "__main__":
    unittest.main()
