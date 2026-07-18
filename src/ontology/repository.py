import json
import hashlib
from typing import Any

from src.ontology.domain.model import OntologySnapshot
from src.ontology.extractor import normalize_alias


class PostgresOntologyRepository:
    """Independent owner-scoped persistence for explicit ontology snapshots."""

    def __init__(self, db_manager: Any):
        self.db_manager = db_manager

    def _owner_id(self) -> str:
        from src.core.config import current_user_config

        return (current_user_config.get() or {}).get("user_id", "SYSTEM")

    def replace_explicit_snapshot(self, snapshot: OntologySnapshot) -> None:
        owner_id = self._owner_id()
        with self.db_manager.transaction() as cur:
            cur.execute(
                "DELETE FROM knowledge_document_concepts WHERE owner_id = %s AND source_path = %s;",
                (owner_id, snapshot.source_path),
            )
            cur.execute(
                "DELETE FROM knowledge_ontology_relations WHERE owner_id = %s AND source_path = %s AND source_kind = 'explicit';",
                (owner_id, snapshot.source_path),
            )
            cur.execute(
                "DELETE FROM knowledge_ontology_aliases WHERE owner_id = %s AND source_path = %s;",
                (owner_id, snapshot.source_path),
            )
            cur.execute(
                "DELETE FROM knowledge_ontology_concept_sources WHERE owner_id = %s AND source_path = %s;",
                (owner_id, snapshot.source_path),
            )

            for concept in snapshot.concepts:
                cur.execute("""
                    INSERT INTO knowledge_ontology_concepts (
                        owner_id, concept_id, canonical_name, concept_kind,
                        description, status, metadata, updated_at
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s::jsonb, CURRENT_TIMESTAMP)
                    ON CONFLICT (owner_id, concept_id) DO UPDATE SET
                        canonical_name = EXCLUDED.canonical_name,
                        concept_kind = EXCLUDED.concept_kind,
                        description = EXCLUDED.description,
                        status = EXCLUDED.status,
                        metadata = EXCLUDED.metadata,
                        updated_at = CURRENT_TIMESTAMP;
                """, (
                    owner_id, concept.concept_id, concept.canonical_name, concept.kind,
                    concept.description, concept.status, json.dumps(dict(concept.metadata)),
                ))
                cur.execute("""
                    INSERT INTO knowledge_ontology_concept_sources (
                        owner_id, concept_id, source_path
                    ) VALUES (%s, %s, %s)
                    ON CONFLICT (owner_id, concept_id, source_path) DO NOTHING;
                """, (owner_id, concept.concept_id, snapshot.source_path))
                for alias in concept.aliases:
                    cur.execute("""
                        INSERT INTO knowledge_ontology_aliases (
                            owner_id, concept_id, alias, normalized_alias, source_path
                        ) VALUES (%s, %s, %s, %s, %s)
                        ON CONFLICT (owner_id, concept_id, normalized_alias) DO UPDATE SET
                            alias = EXCLUDED.alias,
                            source_path = EXCLUDED.source_path;
                    """, (owner_id, concept.concept_id, alias, normalize_alias(alias), snapshot.source_path))

            for relation in snapshot.relations:
                cur.execute("""
                    INSERT INTO knowledge_ontology_relations (
                        owner_id, subject_concept_id, predicate, object_concept_id,
                        status, source_kind, source_path, confidence, scope,
                        valid_from, valid_to, reviewed_by, reviewed_at, review_reason,
                        metadata, updated_at
                    ) VALUES (
                        %s, %s, %s, %s, %s, 'explicit', %s, %s, %s::jsonb,
                        %s, %s, %s, %s, %s, %s::jsonb, CURRENT_TIMESTAMP
                    )
                    ON CONFLICT (
                        owner_id, subject_concept_id, predicate, object_concept_id, source_path
                    ) DO UPDATE SET
                        status = EXCLUDED.status,
                        confidence = EXCLUDED.confidence,
                        scope = EXCLUDED.scope,
                        valid_from = EXCLUDED.valid_from,
                        valid_to = EXCLUDED.valid_to,
                        reviewed_by = EXCLUDED.reviewed_by,
                        reviewed_at = EXCLUDED.reviewed_at,
                        review_reason = EXCLUDED.review_reason,
                        metadata = EXCLUDED.metadata,
                        updated_at = CURRENT_TIMESTAMP;
                """, (
                    owner_id, relation.subject, relation.predicate, relation.object,
                    relation.status, snapshot.source_path, relation.confidence,
                    json.dumps(dict(relation.scope)), relation.valid_from, relation.valid_to,
                    relation.reviewed_by, relation.reviewed_at, relation.review_reason,
                    json.dumps(dict(relation.metadata)),
                ))
                evidence_material = relation.evidence_text or (
                    f"frontmatter:{snapshot.source_path}:{relation.subject}:"
                    f"{relation.predicate}:{relation.object}"
                )
                evidence_hash = relation.evidence_hash or hashlib.sha256(
                    evidence_material.encode("utf-8")
                ).hexdigest()
                cur.execute("""
                    INSERT INTO knowledge_ontology_relation_evidence (
                        relation_id, owner_id, source_path, source_revision,
                        evidence_text, evidence_location, evidence_hash, confidence,
                        extractor_type, model_name, model_version, prompt_version,
                        ontology_schema_version
                    )
                    SELECT
                        relation_id, %s, %s, %s, %s, %s::jsonb, %s, %s,
                        %s, %s, %s, %s, %s
                    FROM knowledge_ontology_relations
                    WHERE owner_id = %s
                      AND subject_concept_id = %s
                      AND predicate = %s
                      AND object_concept_id = %s
                      AND source_path = %s
                    ON CONFLICT (
                        relation_id, source_path, evidence_hash, extractor_type
                    ) DO UPDATE SET
                        source_revision = EXCLUDED.source_revision,
                        evidence_text = EXCLUDED.evidence_text,
                        evidence_location = EXCLUDED.evidence_location,
                        confidence = EXCLUDED.confidence,
                        model_name = EXCLUDED.model_name,
                        model_version = EXCLUDED.model_version,
                        prompt_version = EXCLUDED.prompt_version,
                        ontology_schema_version = EXCLUDED.ontology_schema_version,
                        extracted_at = CURRENT_TIMESTAMP;
                """, (
                    owner_id, snapshot.source_path, relation.source_revision,
                    relation.evidence_text, json.dumps(dict(relation.evidence_location)),
                    evidence_hash, relation.confidence, relation.extractor_type,
                    relation.model_name, relation.model_version, relation.prompt_version,
                    relation.ontology_schema_version, owner_id, relation.subject,
                    relation.predicate, relation.object, snapshot.source_path,
                ))

            for link in snapshot.document_concepts:
                cur.execute("""
                    INSERT INTO knowledge_document_concepts (
                        owner_id, file_path, concept_id, source_kind,
                        source_path, confidence
                    ) VALUES (%s, %s, %s, 'explicit', %s, %s)
                    ON CONFLICT (owner_id, file_path, concept_id) DO UPDATE SET
                        source_kind = EXCLUDED.source_kind,
                        source_path = EXCLUDED.source_path,
                        confidence = EXCLUDED.confidence;
                """, (
                    owner_id, link.file_path, link.concept_id,
                    snapshot.source_path, link.confidence,
                ))

            self._delete_orphan_concepts(cur, owner_id)

    def delete_explicit_source(self, source_path: str) -> None:
        owner_id = self._owner_id()
        with self.db_manager.transaction() as cur:
            cur.execute(
                "DELETE FROM knowledge_document_concepts WHERE owner_id = %s AND source_path = %s;",
                (owner_id, source_path),
            )
            cur.execute(
                "DELETE FROM knowledge_ontology_relations WHERE owner_id = %s AND source_path = %s AND source_kind = 'explicit';",
                (owner_id, source_path),
            )
            cur.execute(
                "DELETE FROM knowledge_ontology_aliases WHERE owner_id = %s AND source_path = %s;",
                (owner_id, source_path),
            )
            cur.execute(
                "DELETE FROM knowledge_ontology_concept_sources WHERE owner_id = %s AND source_path = %s;",
                (owner_id, source_path),
            )
            self._delete_orphan_concepts(cur, owner_id)

    @staticmethod
    def _delete_orphan_concepts(cur: Any, owner_id: str) -> None:
        cur.execute("""
            DELETE FROM knowledge_ontology_concepts c
            WHERE c.owner_id = %s
              AND NOT EXISTS (
                  SELECT 1 FROM knowledge_ontology_concept_sources s
                  WHERE s.owner_id = c.owner_id AND s.concept_id = c.concept_id
              )
              AND NOT EXISTS (
                  SELECT 1 FROM knowledge_ontology_relations r
                  WHERE r.owner_id = c.owner_id
                    AND (r.subject_concept_id = c.concept_id OR r.object_concept_id = c.concept_id)
              );
        """, (owner_id,))

    def record_shadow_event(self, file_path: str, outcome: Any) -> None:
        owner_id = self._owner_id()
        with self.db_manager.cursor() as cur:
            cur.execute("""
                INSERT INTO knowledge_ontology_shadow_events (
                    owner_id, file_path, status, persisted, concept_count,
                    relation_count, document_concept_count, duration_ms,
                    error_type, error_message
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s);
            """, (
                owner_id,
                file_path,
                outcome.status,
                outcome.persisted,
                outcome.concept_count,
                outcome.relation_count,
                outcome.document_concept_count,
                outcome.duration_ms,
                outcome.error_type,
                outcome.error_message,
            ))
