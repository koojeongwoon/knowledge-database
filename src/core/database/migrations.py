from dataclasses import dataclass
from typing import Callable, Iterable

from src.core.config import EMBEDDING_DIM


MigrationOperation = Callable[[object], None]


@dataclass(frozen=True)
class Migration:
    version: int
    name: str
    apply: MigrationOperation


def _create_core_schema(cur) -> None:
    dimension = int(EMBEDDING_DIM)
    if dimension <= 0:
        raise ValueError("EMBEDDING_DIM은 양수여야 합니다.")

    cur.execute("CREATE EXTENSION IF NOT EXISTS vector;")
    cur.execute(f"""
        CREATE TABLE IF NOT EXISTS knowledge_documents (
            id SERIAL PRIMARY KEY,
            file_path VARCHAR(512) NOT NULL,
            chunk_index INT NOT NULL DEFAULT 0,
            doc_type VARCHAR(50) NOT NULL,
            title VARCHAR(256) NOT NULL,
            description TEXT,
            tags TEXT[],
            content TEXT NOT NULL,
            parent_content TEXT NOT NULL,
            raw_frontmatter JSONB,
            content_hash VARCHAR(64) NOT NULL,
            updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
            embedding VECTOR({dimension}),
            owner_id VARCHAR(50) NOT NULL DEFAULT 'SYSTEM',
            visibility VARCHAR(20) NOT NULL DEFAULT 'public',
            CONSTRAINT uq_owner_file_chunk UNIQUE (owner_id, file_path, chunk_index)
        );
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS knowledge_edges (
            id SERIAL PRIMARY KEY,
            source_path VARCHAR(512) NOT NULL,
            target_topic VARCHAR(256) NOT NULL,
            weight REAL NOT NULL DEFAULT 1.0,
            created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
            owner_id VARCHAR(50) NOT NULL DEFAULT 'SYSTEM',
            visibility VARCHAR(20) NOT NULL DEFAULT 'public',
            CONSTRAINT uq_owner_edge UNIQUE (owner_id, source_path, target_topic)
        );
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS knowledge_citations (
            file_path VARCHAR(512) NOT NULL,
            citation_count INT NOT NULL DEFAULT 0,
            last_cited_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
            owner_id VARCHAR(50) NOT NULL DEFAULT 'SYSTEM',
            PRIMARY KEY (owner_id, file_path)
        );
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS knowledge_topics (
            topic_name VARCHAR(256) NOT NULL,
            category VARCHAR(100) NOT NULL,
            file_path VARCHAR(512) NOT NULL,
            updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
            owner_id VARCHAR(50) NOT NULL DEFAULT 'SYSTEM',
            PRIMARY KEY (owner_id, topic_name)
        );
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS knowledge_audit_logs (
            id SERIAL PRIMARY KEY,
            timestamp TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
            user_id VARCHAR(50),
            action VARCHAR(100),
            status VARCHAR(50),
            payload JSONB
        );
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS knowledge_users (
            user_id VARCHAR(50) PRIMARY KEY,
            sub_val VARCHAR(100) UNIQUE NOT NULL,
            created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
        );
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS knowledge_api_keys (
            api_key_hash VARCHAR(255) PRIMARY KEY,
            key_id VARCHAR(36) UNIQUE,
            user_id VARCHAR(50) NOT NULL REFERENCES knowledge_users(user_id) ON DELETE CASCADE,
            key_name VARCHAR(100),
            key_prefix VARCHAR(32),
            expires_at TIMESTAMP WITH TIME ZONE,
            created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
        );
    """)


def _upgrade_legacy_multitenancy(cur) -> None:
    statements = (
        "ALTER TABLE knowledge_api_keys ADD COLUMN IF NOT EXISTS key_id VARCHAR(36) UNIQUE;",
        "ALTER TABLE knowledge_api_keys ADD COLUMN IF NOT EXISTS key_prefix VARCHAR(32);",
        "ALTER TABLE knowledge_documents ADD COLUMN IF NOT EXISTS owner_id VARCHAR(50) NOT NULL DEFAULT 'SYSTEM';",
        "ALTER TABLE knowledge_documents ADD COLUMN IF NOT EXISTS visibility VARCHAR(20) NOT NULL DEFAULT 'public';",
        "ALTER TABLE knowledge_edges ADD COLUMN IF NOT EXISTS owner_id VARCHAR(50) NOT NULL DEFAULT 'SYSTEM';",
        "ALTER TABLE knowledge_edges ADD COLUMN IF NOT EXISTS visibility VARCHAR(20) NOT NULL DEFAULT 'public';",
        "ALTER TABLE knowledge_edges ADD COLUMN IF NOT EXISTS weight REAL NOT NULL DEFAULT 1.0;",
        "ALTER TABLE knowledge_topics ADD COLUMN IF NOT EXISTS owner_id VARCHAR(50) NOT NULL DEFAULT 'SYSTEM';",
        "ALTER TABLE knowledge_citations ADD COLUMN IF NOT EXISTS owner_id VARCHAR(50) NOT NULL DEFAULT 'SYSTEM';",
        "ALTER TABLE knowledge_documents DROP CONSTRAINT IF EXISTS uq_file_chunk;",
        "ALTER TABLE knowledge_documents DROP CONSTRAINT IF EXISTS uq_owner_file_chunk;",
        "ALTER TABLE knowledge_documents ADD CONSTRAINT uq_owner_file_chunk UNIQUE (owner_id, file_path, chunk_index);",
        "ALTER TABLE knowledge_edges DROP CONSTRAINT IF EXISTS uq_edge;",
        "ALTER TABLE knowledge_edges DROP CONSTRAINT IF EXISTS uq_owner_edge;",
        "ALTER TABLE knowledge_edges ADD CONSTRAINT uq_owner_edge UNIQUE (owner_id, source_path, target_topic);",
        "ALTER TABLE knowledge_topics DROP CONSTRAINT IF EXISTS knowledge_topics_pkey;",
        "ALTER TABLE knowledge_topics ADD PRIMARY KEY (owner_id, topic_name);",
        "ALTER TABLE knowledge_citations DROP CONSTRAINT IF EXISTS knowledge_citations_pkey;",
        "ALTER TABLE knowledge_citations ADD PRIMARY KEY (owner_id, file_path);",
    )
    for statement in statements:
        cur.execute(statement)


def _create_indexing_jobs(cur) -> None:
    cur.execute("""
        CREATE TABLE IF NOT EXISTS knowledge_indexing_jobs (
            id BIGSERIAL PRIMARY KEY,
            owner_id VARCHAR(50) NOT NULL,
            file_path VARCHAR(512) NOT NULL,
            status VARCHAR(20) NOT NULL DEFAULT 'pending',
            attempts INT NOT NULL DEFAULT 0,
            last_error TEXT,
            next_retry_at TIMESTAMP WITH TIME ZONE,
            created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
            CONSTRAINT uq_owner_indexing_job UNIQUE (owner_id, file_path)
        );
    """)
    cur.execute("""
        CREATE INDEX IF NOT EXISTS knowledge_indexing_jobs_retry_idx
        ON knowledge_indexing_jobs (owner_id, status, next_retry_at);
    """)


def _create_user_settings(cur) -> None:
    cur.execute("""
        CREATE TABLE IF NOT EXISTS knowledge_user_settings (
            owner_id VARCHAR(50) PRIMARY KEY,
            openai_api_key_encrypted TEXT,
            storage_type VARCHAR(20) NOT NULL DEFAULT 's3',
            s3_endpoint_url TEXT,
            s3_bucket_name TEXT,
            s3_access_key_id_encrypted TEXT,
            s3_secret_access_key_encrypted TEXT,
            updated_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
    """)


def _create_search_indexes(cur) -> None:
    cur.execute("""
        CREATE INDEX IF NOT EXISTS knowledge_documents_embedding_idx
        ON knowledge_documents USING hnsw (embedding vector_cosine_ops);
    """)
    cur.execute("""
        CREATE INDEX IF NOT EXISTS knowledge_documents_fts_idx
        ON knowledge_documents USING gin (
            to_tsvector('simple', coalesce(content, '') || ' ' || coalesce(title, ''))
        );
    """)


def _require_remote_user_storage(cur) -> None:
    cur.execute("""
        ALTER TABLE knowledge_user_settings
        ALTER COLUMN storage_type SET DEFAULT 'r2';
    """)


def _default_to_s3_storage(cur) -> None:
    cur.execute("""
        ALTER TABLE knowledge_user_settings
        ALTER COLUMN storage_type SET DEFAULT 's3';
    """)


def _create_search_feedback(cur) -> None:
    cur.execute("""
        CREATE TABLE IF NOT EXISTS knowledge_search_events (
            search_id UUID PRIMARY KEY,
            owner_id VARCHAR(50) NOT NULL,
            query_text TEXT NOT NULL,
            query_hash CHAR(64) NOT NULL,
            returned_results JSONB NOT NULL DEFAULT '[]'::jsonb,
            result_count INT NOT NULL DEFAULT 0,
            pipeline_version VARCHAR(100) NOT NULL,
            created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
    """)
    cur.execute("""
        CREATE INDEX IF NOT EXISTS knowledge_search_events_owner_created_idx
        ON knowledge_search_events (owner_id, created_at DESC);
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS knowledge_search_feedback (
            id BIGSERIAL PRIMARY KEY,
            search_id UUID NOT NULL REFERENCES knowledge_search_events(search_id) ON DELETE CASCADE,
            owner_id VARCHAR(50) NOT NULL,
            relevant_paths TEXT[] NOT NULL DEFAULT '{}',
            irrelevant_paths TEXT[] NOT NULL DEFAULT '{}',
            expected_no_answer BOOLEAN NOT NULL DEFAULT FALSE,
            missing_answer_path TEXT,
            notes TEXT,
            labeled_by VARCHAR(50) NOT NULL,
            labeled_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
            CONSTRAINT uq_owner_search_feedback UNIQUE (owner_id, search_id),
            CONSTRAINT ck_feedback_no_answer CHECK (
                NOT expected_no_answer OR cardinality(relevant_paths) = 0
            )
        );
    """)


def _extend_search_feedback_labels(cur) -> None:
    cur.execute("""
        ALTER TABLE knowledge_search_feedback
        ADD COLUMN IF NOT EXISTS partially_relevant_paths TEXT[] NOT NULL DEFAULT '{}',
        ADD COLUMN IF NOT EXISTS satisfaction VARCHAR(20),
        ADD COLUMN IF NOT EXISTS failure_reasons TEXT[] NOT NULL DEFAULT '{}';
    """)
    cur.execute("""
        ALTER TABLE knowledge_search_feedback
        DROP CONSTRAINT IF EXISTS ck_feedback_satisfaction;
    """)
    cur.execute("""
        ALTER TABLE knowledge_search_feedback
        ADD CONSTRAINT ck_feedback_satisfaction CHECK (
            satisfaction IS NULL OR satisfaction IN ('satisfied', 'partial', 'dissatisfied')
        );
    """)


def _create_search_feedback_telemetry(cur) -> None:
    cur.execute("""
        ALTER TABLE knowledge_search_events
        ADD COLUMN IF NOT EXISTS ranking_config_version VARCHAR(100) NOT NULL DEFAULT 'retrieval-v1',
        ADD COLUMN IF NOT EXISTS ontology_version VARCHAR(100) NOT NULL DEFAULT 'none',
        ADD COLUMN IF NOT EXISTS query_intent_snapshot JSONB NOT NULL DEFAULT '{}'::jsonb,
        ADD COLUMN IF NOT EXISTS candidate_count INT NOT NULL DEFAULT 0,
        ADD COLUMN IF NOT EXISTS trace_sampled BOOLEAN NOT NULL DEFAULT TRUE,
        ADD COLUMN IF NOT EXISTS reformulated_from_search_id UUID REFERENCES knowledge_search_events(search_id);
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS knowledge_search_candidates (
            id BIGSERIAL PRIMARY KEY,
            search_id UUID NOT NULL REFERENCES knowledge_search_events(search_id) ON DELETE CASCADE,
            owner_id VARCHAR(50) NOT NULL,
            file_path TEXT NOT NULL,
            chunk_index INT,
            retrieval_sources TEXT[] NOT NULL DEFAULT '{}',
            retrieval_kind VARCHAR(20) NOT NULL DEFAULT 'direct',
            vector_score REAL NOT NULL DEFAULT 0,
            lexical_score REAL NOT NULL DEFAULT 0,
            rrf_score REAL NOT NULL DEFAULT 0,
            reranker_score REAL,
            pre_rule_rank INT,
            final_rank INT,
            exposed BOOLEAN NOT NULL DEFAULT FALSE,
            matched_concept_ids TEXT[] NOT NULL DEFAULT '{}',
            relation_path JSONB NOT NULL DEFAULT '[]'::jsonb,
            applied_rule_ids TEXT[] NOT NULL DEFAULT '{}',
            decision VARCHAR(20) NOT NULL DEFAULT 'include',
            decision_reasons TEXT[] NOT NULL DEFAULT '{}',
            score_components JSONB NOT NULL DEFAULT '{}'::jsonb,
            created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
            CONSTRAINT ck_search_candidate_decision CHECK (
                decision IN ('include', 'boost', 'demote', 'exclude')
            )
        );
    """)
    cur.execute("""
        CREATE INDEX IF NOT EXISTS knowledge_search_candidates_owner_search_idx
        ON knowledge_search_candidates (owner_id, search_id);
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS knowledge_search_result_feedback (
            id BIGSERIAL PRIMARY KEY,
            search_id UUID NOT NULL REFERENCES knowledge_search_events(search_id) ON DELETE CASCADE,
            owner_id VARCHAR(50) NOT NULL,
            file_path TEXT NOT NULL,
            relevance_grade SMALLINT NOT NULL,
            issue_reasons TEXT[] NOT NULL DEFAULT '{}',
            preferred_replacement_path TEXT,
            relation_helpful BOOLEAN,
            notes TEXT,
            labeled_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
            CONSTRAINT uq_search_result_feedback UNIQUE (owner_id, search_id, file_path),
            CONSTRAINT ck_search_result_relevance_grade CHECK (relevance_grade BETWEEN 0 AND 3)
        );
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS knowledge_search_behavior_events (
            id BIGSERIAL PRIMARY KEY,
            search_id UUID NOT NULL REFERENCES knowledge_search_events(search_id) ON DELETE CASCADE,
            owner_id VARCHAR(50) NOT NULL,
            file_path TEXT,
            action VARCHAR(30) NOT NULL,
            position INT,
            occurred_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
            CONSTRAINT ck_search_behavior_action CHECK (
                action IN ('open', 'copy', 'cite', 'follow_graph', 'reformulate', 'abandon')
            )
        );
    """)
    cur.execute("""
        CREATE INDEX IF NOT EXISTS knowledge_search_behavior_owner_search_idx
        ON knowledge_search_behavior_events (owner_id, search_id, occurred_at);
    """)


def _create_learning_sessions(cur) -> None:
    cur.execute("""
        CREATE TABLE IF NOT EXISTS knowledge_learning_sessions (
            session_id UUID PRIMARY KEY,
            owner_id VARCHAR(50) NOT NULL,
            client_request_id VARCHAR(100),
            topic VARCHAR(300) NOT NULL,
            requested_scope VARCHAR(20) NOT NULL,
            effective_scope VARCHAR(20) NOT NULL,
            goal VARCHAR(500) NOT NULL,
            level VARCHAR(20) NOT NULL,
            duration_minutes INT NOT NULL,
            status VARCHAR(20) NOT NULL DEFAULT 'active',
            plan_snapshot JSONB NOT NULL DEFAULT '{}'::jsonb,
            completion_summary TEXT,
            started_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
            completed_at TIMESTAMP WITH TIME ZONE,
            CONSTRAINT uq_learning_session_request UNIQUE (owner_id, client_request_id),
            CONSTRAINT ck_learning_requested_scope CHECK (requested_scope IN ('inbox', 'knowledge', 'combined')),
            CONSTRAINT ck_learning_effective_scope CHECK (effective_scope IN ('none', 'inbox', 'knowledge', 'combined')),
            CONSTRAINT ck_learning_level CHECK (level IN ('beginner', 'practical', 'advanced')),
            CONSTRAINT ck_learning_duration CHECK (duration_minutes BETWEEN 5 AND 120),
            CONSTRAINT ck_learning_session_status CHECK (status IN ('active', 'completed'))
        );
    """)
    cur.execute("""
        CREATE INDEX IF NOT EXISTS knowledge_learning_sessions_owner_updated_idx
        ON knowledge_learning_sessions (owner_id, status, updated_at DESC);
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS knowledge_learning_questions (
            question_id UUID PRIMARY KEY,
            session_id UUID NOT NULL REFERENCES knowledge_learning_sessions(session_id) ON DELETE CASCADE,
            owner_id VARCHAR(50) NOT NULL,
            sequence_no INT NOT NULL,
            question_type VARCHAR(30) NOT NULL,
            prompt TEXT NOT NULL,
            evidence_refs TEXT[] NOT NULL DEFAULT '{}',
            created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
            CONSTRAINT uq_learning_question_sequence UNIQUE (session_id, sequence_no),
            CONSTRAINT ck_learning_question_sequence CHECK (sequence_no > 0)
        );
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS knowledge_learning_attempts (
            attempt_id UUID PRIMARY KEY,
            session_id UUID NOT NULL REFERENCES knowledge_learning_sessions(session_id) ON DELETE CASCADE,
            question_id UUID NOT NULL REFERENCES knowledge_learning_questions(question_id) ON DELETE CASCADE,
            owner_id VARCHAR(50) NOT NULL,
            client_request_id VARCHAR(100),
            answer TEXT NOT NULL,
            assessment VARCHAR(20) NOT NULL,
            confidence VARCHAR(20) NOT NULL,
            missing_concepts TEXT[] NOT NULL DEFAULT '{}',
            misconceptions TEXT[] NOT NULL DEFAULT '{}',
            evidence_refs TEXT[] NOT NULL DEFAULT '{}',
            feedback_plan JSONB NOT NULL DEFAULT '{}'::jsonb,
            created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
            CONSTRAINT uq_learning_attempt_request UNIQUE (owner_id, client_request_id),
            CONSTRAINT ck_learning_attempt_assessment CHECK (
                assessment IN ('mastered', 'partial', 'misconception', 'unknown', 'unverifiable')
            ),
            CONSTRAINT ck_learning_attempt_confidence CHECK (confidence IN ('low', 'medium', 'high'))
        );
    """)
    cur.execute("""
        CREATE INDEX IF NOT EXISTS knowledge_learning_attempts_session_idx
        ON knowledge_learning_attempts (owner_id, session_id, created_at);
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS knowledge_learning_sources (
            session_id UUID NOT NULL REFERENCES knowledge_learning_sessions(session_id) ON DELETE CASCADE,
            owner_id VARCHAR(50) NOT NULL,
            source_type VARCHAR(20) NOT NULL,
            source_ref TEXT NOT NULL,
            relationship VARCHAR(20),
            snapshot_hash CHAR(64) NOT NULL,
            metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
            created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (session_id, source_type, source_ref),
            CONSTRAINT ck_learning_source_type CHECK (source_type IN ('inbox', 'knowledge')),
            CONSTRAINT ck_learning_source_relationship CHECK (
                relationship IS NULL OR relationship IN ('confirm', 'extend', 'conflict', 'replace', 'unresolved')
            )
        );
    """)


def _create_learning_reviews(cur) -> None:
    cur.execute("""
        CREATE TABLE IF NOT EXISTS knowledge_learning_reviews (
            review_id UUID PRIMARY KEY,
            owner_id VARCHAR(50) NOT NULL,
            session_id UUID NOT NULL REFERENCES knowledge_learning_sessions(session_id) ON DELETE CASCADE,
            question_id UUID NOT NULL REFERENCES knowledge_learning_questions(question_id) ON DELETE CASCADE,
            source_attempt_id UUID NOT NULL REFERENCES knowledge_learning_attempts(attempt_id) ON DELETE CASCADE,
            topic VARCHAR(300) NOT NULL,
            prompt TEXT NOT NULL,
            evidence_refs TEXT[] NOT NULL DEFAULT '{}',
            review_priority VARCHAR(20) NOT NULL,
            interval_days INT NOT NULL,
            due_at TIMESTAMP WITH TIME ZONE NOT NULL,
            status VARCHAR(20) NOT NULL DEFAULT 'scheduled',
            review_count INT NOT NULL DEFAULT 0,
            last_reviewed_at TIMESTAMP WITH TIME ZONE,
            created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
            CONSTRAINT uq_learning_review_question UNIQUE (owner_id, question_id),
            CONSTRAINT ck_learning_review_priority CHECK (
                review_priority IN ('low', 'medium', 'high', 'critical', 'blocked')
            ),
            CONSTRAINT ck_learning_review_interval CHECK (interval_days BETWEEN 1 AND 365),
            CONSTRAINT ck_learning_review_status CHECK (status IN ('scheduled', 'paused'))
        );
    """)
    cur.execute("""
        CREATE INDEX IF NOT EXISTS knowledge_learning_reviews_due_idx
        ON knowledge_learning_reviews (owner_id, status, due_at);
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS knowledge_learning_review_attempts (
            review_attempt_id UUID PRIMARY KEY,
            review_id UUID NOT NULL REFERENCES knowledge_learning_reviews(review_id) ON DELETE CASCADE,
            owner_id VARCHAR(50) NOT NULL,
            client_request_id VARCHAR(100),
            answer TEXT NOT NULL,
            assessment VARCHAR(20) NOT NULL,
            confidence VARCHAR(20) NOT NULL,
            feedback_plan JSONB NOT NULL DEFAULT '{}'::jsonb,
            previous_interval_days INT NOT NULL,
            next_interval_days INT NOT NULL,
            reviewed_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
            CONSTRAINT uq_learning_review_attempt_request UNIQUE (owner_id, client_request_id),
            CONSTRAINT ck_learning_review_assessment CHECK (
                assessment IN ('mastered', 'partial', 'misconception', 'unknown', 'unverifiable')
            ),
            CONSTRAINT ck_learning_review_confidence CHECK (confidence IN ('low', 'medium', 'high')),
            CONSTRAINT ck_learning_review_previous_interval CHECK (previous_interval_days BETWEEN 1 AND 365),
            CONSTRAINT ck_learning_review_next_interval CHECK (next_interval_days BETWEEN 1 AND 365)
        );
    """)


def _create_learning_knowledge_candidates(cur) -> None:
    cur.execute("""
        CREATE TABLE IF NOT EXISTS knowledge_learning_knowledge_candidates (
            candidate_id UUID PRIMARY KEY,
            owner_id VARCHAR(50) NOT NULL,
            session_id UUID NOT NULL REFERENCES knowledge_learning_sessions(session_id) ON DELETE CASCADE,
            client_request_id VARCHAR(100),
            candidate_type VARCHAR(30) NOT NULL,
            title VARCHAR(300) NOT NULL,
            description TEXT NOT NULL,
            tags TEXT[] NOT NULL DEFAULT '{}',
            content TEXT NOT NULL,
            topic_name VARCHAR(256),
            topic_update_text TEXT,
            evidence_refs TEXT[] NOT NULL DEFAULT '{}',
            content_hash CHAR(64) NOT NULL,
            status VARCHAR(20) NOT NULL DEFAULT 'pending',
            approval_note TEXT,
            approved_at TIMESTAMP WITH TIME ZONE,
            rejected_at TIMESTAMP WITH TIME ZONE,
            committed_at TIMESTAMP WITH TIME ZONE,
            qa_file_path TEXT,
            topic_file_path TEXT,
            created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
            CONSTRAINT uq_learning_candidate_request UNIQUE (owner_id, client_request_id),
            CONSTRAINT ck_learning_candidate_type CHECK (
                candidate_type IN ('learning_record', 'inbox_promotion', 'knowledge_correction', 'unresolved_question')
            ),
            CONSTRAINT ck_learning_candidate_status CHECK (
                status IN ('pending', 'approved', 'rejected', 'committing', 'committed')
            )
        );
    """)
    cur.execute("""
        CREATE INDEX IF NOT EXISTS knowledge_learning_candidates_owner_session_idx
        ON knowledge_learning_knowledge_candidates (owner_id, session_id, status, created_at);
    """)


def _create_ontology_schema(cur) -> None:
    """Create owner-scoped ontology storage without touching direct retrieval tables."""
    cur.execute("""
        CREATE TABLE IF NOT EXISTS knowledge_ontology_concepts (
            owner_id VARCHAR(50) NOT NULL,
            concept_id VARCHAR(160) NOT NULL,
            canonical_name VARCHAR(300) NOT NULL,
            concept_kind VARCHAR(40) NOT NULL DEFAULT 'concept',
            description TEXT,
            status VARCHAR(20) NOT NULL DEFAULT 'approved',
            metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
            created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (owner_id, concept_id),
            CONSTRAINT ck_ontology_concept_status CHECK (
                status IN ('draft', 'approved', 'deprecated')
            )
        );
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS knowledge_ontology_concept_sources (
            owner_id VARCHAR(50) NOT NULL,
            concept_id VARCHAR(160) NOT NULL,
            source_path VARCHAR(512) NOT NULL,
            created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (owner_id, concept_id, source_path),
            FOREIGN KEY (owner_id, concept_id)
                REFERENCES knowledge_ontology_concepts(owner_id, concept_id)
                ON DELETE CASCADE
        );
    """)
    cur.execute("""
        CREATE INDEX IF NOT EXISTS knowledge_ontology_concept_source_path_idx
        ON knowledge_ontology_concept_sources (owner_id, source_path);
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS knowledge_ontology_aliases (
            owner_id VARCHAR(50) NOT NULL,
            concept_id VARCHAR(160) NOT NULL,
            alias VARCHAR(300) NOT NULL,
            normalized_alias VARCHAR(300) NOT NULL,
            language VARCHAR(20),
            source_path VARCHAR(512) NOT NULL,
            created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (owner_id, concept_id, normalized_alias),
            FOREIGN KEY (owner_id, concept_id)
                REFERENCES knowledge_ontology_concepts(owner_id, concept_id)
                ON DELETE CASCADE
        );
    """)
    cur.execute("""
        CREATE INDEX IF NOT EXISTS knowledge_ontology_alias_lookup_idx
        ON knowledge_ontology_aliases (owner_id, normalized_alias);
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS knowledge_ontology_relations (
            owner_id VARCHAR(50) NOT NULL,
            subject_concept_id VARCHAR(160) NOT NULL,
            predicate VARCHAR(40) NOT NULL,
            object_concept_id VARCHAR(160) NOT NULL,
            status VARCHAR(20) NOT NULL DEFAULT 'approved',
            source_kind VARCHAR(20) NOT NULL DEFAULT 'explicit',
            source_path VARCHAR(512) NOT NULL,
            confidence REAL NOT NULL DEFAULT 1.0,
            metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
            created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (owner_id, subject_concept_id, predicate, object_concept_id, source_path),
            FOREIGN KEY (owner_id, subject_concept_id)
                REFERENCES knowledge_ontology_concepts(owner_id, concept_id),
            FOREIGN KEY (owner_id, object_concept_id)
                REFERENCES knowledge_ontology_concepts(owner_id, concept_id),
            CONSTRAINT ck_ontology_relation_predicate CHECK (
                predicate IN (
                    'uses', 'depends_on', 'is_a', 'part_of', 'supersedes',
                    'contradicts', 'prohibits', 'requires', 'related_to'
                )
            ),
            CONSTRAINT ck_ontology_relation_status CHECK (
                status IN ('draft', 'approved', 'rejected', 'deprecated')
            ),
            CONSTRAINT ck_ontology_relation_source_kind CHECK (
                source_kind IN ('explicit', 'derived')
            ),
            CONSTRAINT ck_ontology_relation_confidence CHECK (
                confidence >= 0 AND confidence <= 1
            )
        );
    """)
    cur.execute("""
        CREATE INDEX IF NOT EXISTS knowledge_ontology_relation_subject_idx
        ON knowledge_ontology_relations (owner_id, subject_concept_id, predicate);
    """)
    cur.execute("""
        CREATE INDEX IF NOT EXISTS knowledge_ontology_relation_object_idx
        ON knowledge_ontology_relations (owner_id, object_concept_id, predicate);
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS knowledge_document_concepts (
            owner_id VARCHAR(50) NOT NULL,
            file_path VARCHAR(512) NOT NULL,
            concept_id VARCHAR(160) NOT NULL,
            source_kind VARCHAR(20) NOT NULL DEFAULT 'explicit',
            source_path VARCHAR(512) NOT NULL,
            confidence REAL NOT NULL DEFAULT 1.0,
            created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (owner_id, file_path, concept_id),
            FOREIGN KEY (owner_id, concept_id)
                REFERENCES knowledge_ontology_concepts(owner_id, concept_id)
                ON DELETE CASCADE,
            CONSTRAINT ck_document_concept_source_kind CHECK (
                source_kind IN ('explicit', 'derived')
            ),
            CONSTRAINT ck_document_concept_confidence CHECK (
                confidence >= 0 AND confidence <= 1
            )
        );
    """)
    cur.execute("""
        CREATE INDEX IF NOT EXISTS knowledge_document_concepts_lookup_idx
        ON knowledge_document_concepts (owner_id, concept_id, file_path);
    """)


def _create_ontology_shadow_events(cur) -> None:
    cur.execute("""
        CREATE TABLE IF NOT EXISTS knowledge_ontology_shadow_events (
            id BIGSERIAL PRIMARY KEY,
            owner_id VARCHAR(50) NOT NULL,
            file_path VARCHAR(512) NOT NULL,
            status VARCHAR(20) NOT NULL,
            persisted BOOLEAN NOT NULL DEFAULT FALSE,
            concept_count INT NOT NULL DEFAULT 0,
            relation_count INT NOT NULL DEFAULT 0,
            document_concept_count INT NOT NULL DEFAULT 0,
            duration_ms REAL NOT NULL DEFAULT 0,
            error_type VARCHAR(160),
            error_message TEXT,
            created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
            CONSTRAINT ck_ontology_shadow_status CHECK (
                status IN ('observed', 'persisted', 'error')
            )
        );
    """)
    cur.execute("""
        CREATE INDEX IF NOT EXISTS knowledge_ontology_shadow_owner_created_idx
        ON knowledge_ontology_shadow_events (owner_id, created_at DESC);
    """)
    cur.execute("""
        CREATE INDEX IF NOT EXISTS knowledge_ontology_shadow_owner_file_idx
        ON knowledge_ontology_shadow_events (owner_id, file_path, created_at DESC);
    """)


def _extend_ontology_relation_lifecycle(cur) -> None:
    """Prepare relation provenance and review lifecycle before automatic extraction."""
    cur.execute("""
        ALTER TABLE knowledge_ontology_relations
        ADD COLUMN IF NOT EXISTS relation_id BIGSERIAL,
        ADD COLUMN IF NOT EXISTS scope JSONB NOT NULL DEFAULT '{}'::jsonb,
        ADD COLUMN IF NOT EXISTS valid_from TIMESTAMP WITH TIME ZONE,
        ADD COLUMN IF NOT EXISTS valid_to TIMESTAMP WITH TIME ZONE,
        ADD COLUMN IF NOT EXISTS reviewed_by VARCHAR(50),
        ADD COLUMN IF NOT EXISTS reviewed_at TIMESTAMP WITH TIME ZONE,
        ADD COLUMN IF NOT EXISTS review_reason TEXT;
    """)
    cur.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS knowledge_ontology_relation_id_idx
        ON knowledge_ontology_relations (relation_id);
    """)
    cur.execute("""
        ALTER TABLE knowledge_ontology_relations
        DROP CONSTRAINT IF EXISTS ck_ontology_relation_status;
    """)
    cur.execute("""
        UPDATE knowledge_ontology_relations
        SET status = CASE status
            WHEN 'approved' THEN 'asserted'
            WHEN 'draft' THEN 'pending'
            WHEN 'deprecated' THEN 'revoked'
            ELSE status
        END;
    """)
    cur.execute("""
        ALTER TABLE knowledge_ontology_relations
        ALTER COLUMN status SET DEFAULT 'asserted',
        ADD CONSTRAINT ck_ontology_relation_status CHECK (
            status IN ('inferred', 'pending', 'asserted', 'rejected', 'stale', 'revoked')
        ),
        ADD CONSTRAINT ck_ontology_relation_validity CHECK (
            valid_from IS NULL OR valid_to IS NULL OR valid_from <= valid_to
        );
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS knowledge_ontology_relation_evidence (
            evidence_id BIGSERIAL PRIMARY KEY,
            relation_id BIGINT NOT NULL REFERENCES knowledge_ontology_relations(relation_id) ON DELETE CASCADE,
            owner_id VARCHAR(50) NOT NULL,
            source_path VARCHAR(512) NOT NULL,
            source_revision VARCHAR(128),
            evidence_text TEXT NOT NULL DEFAULT '',
            evidence_location JSONB NOT NULL DEFAULT '{}'::jsonb,
            evidence_hash CHAR(64) NOT NULL,
            confidence REAL NOT NULL DEFAULT 1.0,
            extractor_type VARCHAR(20) NOT NULL DEFAULT 'human',
            model_name VARCHAR(160),
            model_version VARCHAR(160),
            prompt_version VARCHAR(160),
            ontology_schema_version VARCHAR(100) NOT NULL DEFAULT 'ontology-v1',
            extracted_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
            created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
            CONSTRAINT uq_ontology_relation_evidence UNIQUE (
                relation_id, source_path, evidence_hash, extractor_type
            ),
            CONSTRAINT ck_ontology_evidence_confidence CHECK (
                confidence >= 0 AND confidence <= 1
            ),
            CONSTRAINT ck_ontology_evidence_extractor CHECK (
                extractor_type IN ('human', 'llm', 'rule')
            )
        );
    """)
    cur.execute("""
        CREATE INDEX IF NOT EXISTS knowledge_ontology_evidence_owner_source_idx
        ON knowledge_ontology_relation_evidence (owner_id, source_path);
    """)


def _extend_ontology_search_feedback(cur) -> None:
    """Collect explicit ontology labels before ontology affects retrieval."""
    cur.execute("""
        ALTER TABLE knowledge_search_feedback
        ADD COLUMN IF NOT EXISTS expected_relations JSONB NOT NULL DEFAULT '[]'::jsonb,
        ADD COLUMN IF NOT EXISTS expected_graph_paths JSONB NOT NULL DEFAULT '[]'::jsonb,
        ADD COLUMN IF NOT EXISTS forbidden_paths TEXT[] NOT NULL DEFAULT '{}',
        ADD COLUMN IF NOT EXISTS expected_rule_types TEXT[] NOT NULL DEFAULT '{}',
        ADD COLUMN IF NOT EXISTS ontology_notes TEXT;
    """)
    cur.execute("""
        ALTER TABLE knowledge_search_result_feedback
        ADD COLUMN IF NOT EXISTS ontology_context_grade SMALLINT,
        ADD COLUMN IF NOT EXISTS relation_path_correct BOOLEAN,
        ADD COLUMN IF NOT EXISTS rule_application_correct BOOLEAN;
    """)
    cur.execute("""
        ALTER TABLE knowledge_search_result_feedback
        DROP CONSTRAINT IF EXISTS ck_search_result_ontology_context_grade;
    """)
    cur.execute("""
        ALTER TABLE knowledge_search_result_feedback
        ADD CONSTRAINT ck_search_result_ontology_context_grade CHECK (
            ontology_context_grade IS NULL OR ontology_context_grade BETWEEN 0 AND 3
        );
    """)


MIGRATIONS: tuple[Migration, ...] = (
    Migration(1, "create_core_schema", _create_core_schema),
    Migration(2, "upgrade_legacy_multitenancy", _upgrade_legacy_multitenancy),
    Migration(3, "create_indexing_jobs", _create_indexing_jobs),
    Migration(4, "create_user_settings", _create_user_settings),
    Migration(5, "create_search_indexes", _create_search_indexes),
    Migration(6, "require_remote_user_storage", _require_remote_user_storage),
    Migration(7, "default_to_s3_storage", _default_to_s3_storage),
    Migration(8, "create_search_feedback", _create_search_feedback),
    Migration(9, "extend_search_feedback_labels", _extend_search_feedback_labels),
    Migration(10, "create_search_feedback_telemetry", _create_search_feedback_telemetry),
    Migration(11, "create_learning_sessions", _create_learning_sessions),
    Migration(12, "create_learning_reviews", _create_learning_reviews),
    Migration(13, "create_learning_knowledge_candidates", _create_learning_knowledge_candidates),
    Migration(14, "create_ontology_schema", _create_ontology_schema),
    Migration(15, "create_ontology_shadow_events", _create_ontology_shadow_events),
    Migration(16, "extend_ontology_relation_lifecycle", _extend_ontology_relation_lifecycle),
    Migration(17, "extend_ontology_search_feedback", _extend_ontology_search_feedback),
)


def run_database_migrations(db_manager, migrations: Iterable[Migration] = MIGRATIONS) -> list[int]:
    """미적용 스키마 변경을 버전 순서대로 하나의 트랜잭션에서 적용합니다."""
    applied_now: list[int] = []
    ordered = sorted(migrations, key=lambda migration: migration.version)
    if len({migration.version for migration in ordered}) != len(ordered):
        raise ValueError("마이그레이션 버전은 중복될 수 없습니다.")

    with db_manager.transaction() as cur:
        cur.execute("SELECT pg_advisory_xact_lock(7216042026);")
        cur.execute("""
            CREATE TABLE IF NOT EXISTS knowledge_schema_migrations (
                version INTEGER PRIMARY KEY,
                name VARCHAR(255) NOT NULL,
                applied_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
        """)
        cur.execute("SELECT version FROM knowledge_schema_migrations;")
        applied = {row[0] for row in cur.fetchall()}

        for migration in ordered:
            if migration.version in applied:
                continue
            migration.apply(cur)
            cur.execute(
                "INSERT INTO knowledge_schema_migrations (version, name) VALUES (%s, %s);",
                (migration.version, migration.name),
            )
            applied_now.append(migration.version)

    return applied_now
