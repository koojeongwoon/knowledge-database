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
