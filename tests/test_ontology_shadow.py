import unittest
from contextlib import contextmanager

from src.core.config import Settings, current_user_config
from src.ontology.extractor import ExplicitOntologyExtractor, normalize_alias
from src.ontology.repository import PostgresOntologyRepository
from src.ontology.service import OntologyShadowService


class FakeCursor:
    def __init__(self):
        self.calls = []

    def execute(self, sql, params=None):
        self.calls.append((" ".join(sql.split()), params))


class FakeDatabaseManager:
    def __init__(self):
        self.cursor = FakeCursor()

    @contextmanager
    def transaction(self):
        yield self.cursor


class RecordingOntologyRepository:
    def __init__(self):
        self.snapshots = []
        self.events = []
        self.deleted_sources = []

    def replace_explicit_snapshot(self, snapshot):
        self.snapshots.append(snapshot)

    def record_shadow_event(self, file_path, outcome):
        self.events.append((file_path, outcome))

    def delete_explicit_source(self, file_path):
        self.deleted_sources.append(file_path)


class OntologyShadowTests(unittest.TestCase):
    def test_all_rollout_flags_default_to_off(self):
        settings = Settings(_env_file=None)
        self.assertFalse(settings.ontology_indexing_enabled)
        self.assertFalse(settings.ontology_shadow_enabled)
        self.assertFalse(settings.ontology_context_enabled)
        self.assertFalse(settings.ontology_ranking_enabled)
        self.assertFalse(settings.ontology_hard_rules_enabled)

    def test_extracts_only_explicit_frontmatter(self):
        extractor = ExplicitOntologyExtractor()
        empty = extractor.extract("qa/plain.md", {"tags": ["postgresql"]})
        self.assertEqual(empty.concepts, ())

        snapshot = extractor.extract("qa/service.md", {"ontology": {
            "concepts": [
                {"id": "Knowledge Service", "name": "Knowledge Service", "aliases": ["KB", " kb "]},
                {"id": "PostgreSQL", "name": "PostgreSQL", "kind": "technology"},
            ],
            "relations": [{
                "subject": "Knowledge Service", "predicate": "uses", "object": "PostgreSQL",
            }],
        }})
        self.assertEqual(snapshot.concepts[0].concept_id, "knowledge-service")
        self.assertEqual(snapshot.concepts[0].aliases, ("kb",))
        self.assertEqual(snapshot.relations[0].object, "postgresql")
        self.assertEqual(snapshot.relations[0].status, "asserted")
        self.assertEqual(len(snapshot.document_concepts), 2)

    def test_relation_lifecycle_and_provenance_fields_are_parsed(self):
        snapshot = ExplicitOntologyExtractor().extract(
            "qa/service.md",
            {"ontology": {
                "concepts": [{"id": "a", "name": "A"}, {"id": "b", "name": "B"}],
                "relations": [{
                    "subject": "a",
                    "predicate": "depends_on",
                    "object": "b",
                    "status": "pending",
                    "scope": {"environment": "production"},
                    "valid_from": "2026-07-01T00:00:00+00:00",
                    "evidence": "A uses B in production.",
                    "evidence_location": {"heading": "Architecture"},
                    "ontology_schema_version": "ontology-v2",
                }],
            }},
            source_revision="content-hash-1",
        )
        relation = snapshot.relations[0]
        self.assertEqual(relation.scope, {"environment": "production"})
        self.assertEqual(relation.source_revision, "content-hash-1")
        self.assertEqual(relation.evidence_location, {"heading": "Architecture"})
        self.assertEqual(relation.ontology_schema_version, "ontology-v2")

    def test_rejects_unknown_predicate_and_undeclared_reference(self):
        extractor = ExplicitOntologyExtractor()
        with self.assertRaisesRegex(ValueError, "Unsupported ontology predicate"):
            extractor.extract("qa/a.md", {"ontology": {
                "concepts": [{"id": "a", "name": "A"}, {"id": "b", "name": "B"}],
                "relations": [{"subject": "a", "predicate": "likes", "object": "b"}],
            }})
        with self.assertRaisesRegex(ValueError, "undeclared concepts"):
            extractor.extract("qa/a.md", {"ontology": {
                "concepts": [{"id": "a", "name": "A"}],
                "relations": [{"subject": "a", "predicate": "uses", "object": "missing"}],
            }})

    def test_alias_normalization_is_deterministic(self):
        self.assertEqual(normalize_alias(" ＭＳＡ  Architecture "), "msa architecture")

    def test_repository_scopes_every_write_by_authenticated_owner(self):
        snapshot = ExplicitOntologyExtractor().extract("qa/service.md", {"ontology": {
            "concepts": [
                {"id": "service", "name": "Service", "aliases": ["svc"]},
                {"id": "database", "name": "Database"},
            ],
            "relations": [{"subject": "service", "predicate": "uses", "object": "database"}],
        }})
        manager = FakeDatabaseManager()
        token = current_user_config.set({"user_id": "owner-1"})
        try:
            PostgresOntologyRepository(manager).replace_explicit_snapshot(snapshot)
        finally:
            current_user_config.reset(token)

        self.assertGreater(len(manager.cursor.calls), 6)
        for _sql, params in manager.cursor.calls:
            self.assertEqual(params[0], "owner-1")
        sql = "\n".join(call[0] for call in manager.cursor.calls)
        self.assertNotIn("knowledge_documents", sql)
        self.assertNotIn("knowledge_search_candidates", sql)

    def test_shadow_service_is_a_strict_noop_when_disabled(self):
        repository = RecordingOntologyRepository()
        outcome = OntologyShadowService(
            repository, shadow_enabled=False, indexing_enabled=True,
        ).process("qa/a.md", {"ontology": "invalid-but-never-read"})
        self.assertFalse(outcome.enabled)
        self.assertFalse(outcome.persisted)
        self.assertEqual(repository.snapshots, [])
        self.assertEqual(repository.events, [])

    def test_shadow_can_observe_without_persisting(self):
        repository = RecordingOntologyRepository()
        outcome = OntologyShadowService(
            repository, shadow_enabled=True, indexing_enabled=False,
        ).process("qa/a.md", {"ontology": {
            "concepts": [{"id": "a", "name": "A"}],
        }})
        self.assertTrue(outcome.enabled)
        self.assertFalse(outcome.persisted)
        self.assertEqual(outcome.concept_count, 1)
        self.assertEqual(repository.snapshots, [])
        self.assertEqual(repository.events, [])

    def test_shadow_persists_only_when_both_flags_allow_it(self):
        repository = RecordingOntologyRepository()
        outcome = OntologyShadowService(
            repository, shadow_enabled=True, indexing_enabled=True,
        ).process("qa/a.md", {"ontology": {
            "concepts": [{"id": "a", "name": "A"}],
        }})
        self.assertTrue(outcome.persisted)
        self.assertEqual(len(repository.snapshots), 1)

    def test_safe_shadow_records_success_and_contains_validation_error(self):
        repository = RecordingOntologyRepository()
        service = OntologyShadowService(
            repository, shadow_enabled=True, indexing_enabled=False,
        )
        success = service.process_safely("qa/a.md", {"ontology": {
            "concepts": [{"id": "a", "name": "A"}],
        }})
        failure = service.process_safely("qa/b.md", {"ontology": {
            "concepts": [{"id": "b", "name": "B"}],
            "relations": [{"subject": "b", "predicate": "invalid", "object": "b"}],
        }})
        self.assertEqual(success.status, "observed")
        self.assertEqual(failure.status, "error")
        self.assertEqual(failure.error_type, "ValueError")
        self.assertEqual(len(repository.events), 2)

    def test_shadow_telemetry_failure_is_best_effort(self):
        repository = RecordingOntologyRepository()
        repository.record_shadow_event = lambda *_args: (_ for _ in ()).throw(RuntimeError("down"))
        outcome = OntologyShadowService(
            repository, shadow_enabled=True, indexing_enabled=False,
        ).process_safely("qa/a.md", {})
        self.assertEqual(outcome.status, "observed")

    def test_deleted_file_cleanup_requires_both_flags(self):
        repository = RecordingOntologyRepository()
        observed = OntologyShadowService(
            repository, shadow_enabled=True, indexing_enabled=False,
        ).delete_safely("qa/a.md")
        persisted = OntologyShadowService(
            repository, shadow_enabled=True, indexing_enabled=True,
        ).delete_safely("qa/a.md")
        self.assertFalse(observed.persisted)
        self.assertTrue(persisted.persisted)
        self.assertEqual(repository.deleted_sources, ["qa/a.md"])


if __name__ == "__main__":
    unittest.main()
