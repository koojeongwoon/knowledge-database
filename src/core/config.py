from pathlib import Path
from typing import Optional
from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Base directory of the knowledge base project
BASE_DIR = Path(__file__).resolve().parent.parent.parent

class Settings(BaseSettings):
    # DB 설정
    db_type: str = Field(default="postgres", validation_alias="DB_TYPE")
    db_host: str = Field(default="localhost", validation_alias="DB_HOST")
    db_port: int = Field(default=5432, validation_alias="DB_PORT")
    db_name: str = Field(default="knowledge_db", validation_alias="DB_NAME")
    db_user: str = Field(default="postgres", validation_alias="DB_USER")
    db_password: str = Field(default="postgres", validation_alias="DB_PASSWORD")
    db_min_connections: int = Field(default=2, validation_alias="DB_MIN_CONNECTIONS")
    db_max_connections: int = Field(default=20, validation_alias="DB_MAX_CONNECTIONS")

    # 임베딩 공급자 설정 (fake, openai, bge-m3)
    embedding_provider: str = Field(default="fake", validation_alias="EMBEDDING_PROVIDER")
    embedding_dim: Optional[int] = Field(default=None, validation_alias="EMBEDDING_DIM")

    # ── Retrieval Pipeline 설정 ──────────────────────────────────────────
    # RRF (Reciprocal Rank Fusion) 파라미터
    rrf_k: int = Field(default=60, validation_alias="RRF_K")

    # 최종 검색 결과 유사도 임계치
    similarity_threshold: float = Field(default=0.35, validation_alias="SIMILARITY_THRESHOLD")

    # PostgreSQL FTS 원시 관련도(ts_rank) 임계치
    lexical_rank_threshold: float = Field(default=0.02, validation_alias="LEXICAL_RANK_THRESHOLD")
    confidence_filter_enabled: bool = Field(default=False, validation_alias="CONFIDENCE_FILTER_ENABLED")
    confidence_weak_vector: float = Field(default=0.38, validation_alias="CONFIDENCE_WEAK_VECTOR")
    confidence_weak_lexical: float = Field(default=0.015, validation_alias="CONFIDENCE_WEAK_LEXICAL")
    confidence_sparse_margin: float = Field(default=0.5, validation_alias="CONFIDENCE_SPARSE_MARGIN")
    graph_context_enabled: bool = Field(default=False, validation_alias="GRAPH_CONTEXT_ENABLED")
    graph_seed_vector_threshold: float = Field(default=0.5, validation_alias="GRAPH_SEED_VECTOR_THRESHOLD")
    graph_seed_lexical_threshold: float = Field(default=0.05, validation_alias="GRAPH_SEED_LEXICAL_THRESHOLD")
    graph_context_limit: int = Field(default=2, validation_alias="GRAPH_CONTEXT_LIMIT")

    # Cross-Encoder 리랭커 설정 (비활성 시 RRF 결과를 그대로 사용)
    reranker_enabled: bool = Field(default=False, validation_alias="RERANKER_ENABLED")
    reranker_model: str = Field(default="BAAI/bge-reranker-v2-m3", validation_alias="RERANKER_MODEL")

    # 문서 확장 (Document Expansion) 설정
    document_expansion_enabled: bool = Field(default=False, validation_alias="DOCUMENT_EXPANSION_ENABLED")

    # Ontology rollout stages. Every stage is off by default so direct retrieval
    # remains byte-for-byte independent until a later promotion decision.
    ontology_indexing_enabled: bool = Field(default=False, validation_alias="ONTOLOGY_INDEXING_ENABLED")
    ontology_shadow_enabled: bool = Field(default=False, validation_alias="ONTOLOGY_SHADOW_ENABLED")
    ontology_context_enabled: bool = Field(default=False, validation_alias="ONTOLOGY_CONTEXT_ENABLED")
    ontology_ranking_enabled: bool = Field(default=False, validation_alias="ONTOLOGY_RANKING_ENABLED")
    ontology_hard_rules_enabled: bool = Field(default=False, validation_alias="ONTOLOGY_HARD_RULES_ENABLED")

    # ── Redis 설정 ────────────────────────────────────────────────────────
    # Redis Cache (세션 공유, 임시 캐싱)
    redis_cache_host: str = Field(default="redis-cache-service.infra.svc.cluster.local", validation_alias="REDIS_CACHE_HOST")
    redis_cache_port: int = Field(default=6379, validation_alias="REDIS_CACHE_PORT")
    redis_cache_password: str = Field(default="", validation_alias="REDIS_CACHE_PASSWORD")

    # Redis Wiki Cache (지식베이스 전용 토큰 캐싱)
    redis_wiki_host: str = Field(default="redis-wiki-cache-service.infra.svc.cluster.local", validation_alias="REDIS_WIKI_HOST")
    redis_wiki_port: int = Field(default=6379, validation_alias="REDIS_WIKI_PORT")
    redis_wiki_password: str = Field(default="", validation_alias="REDIS_WIKI_PASSWORD")

    # Redis Stream (비동기 아웃박스 이벤트 큐)
    redis_stream_host: str = Field(default="redis-stream-service.infra.svc.cluster.local", validation_alias="REDIS_STREAM_HOST")
    redis_stream_port: int = Field(default=6380, validation_alias="REDIS_STREAM_PORT")
    redis_stream_password: str = Field(default="", validation_alias="REDIS_STREAM_PASSWORD")

    model_config = SettingsConfigDict(
        env_file=str(BASE_DIR / ".env"),
        env_file_encoding="utf-8",
        extra="ignore"
    )

    @model_validator(mode="after")
    def set_embedding_dimension(self) -> 'Settings':
        if self.embedding_provider.lower() == "bge-m3":
            self.embedding_dim = 1024
        elif self.embedding_dim is None:
            self.embedding_dim = 1536
        return self

# Initialize settings instance
settings = Settings()

# Module-level variables to preserve exact import compatibility across the app
DB_TYPE = settings.db_type.lower()
DB_HOST = settings.db_host
DB_PORT = settings.db_port
DB_NAME = settings.db_name
DB_USER = settings.db_user
DB_PASSWORD = settings.db_password
DB_MIN_CONNECTIONS = settings.db_min_connections
DB_MAX_CONNECTIONS = settings.db_max_connections

EMBEDDING_PROVIDER = settings.embedding_provider.lower()
EMBEDDING_DIM = settings.embedding_dim

DATABASE_URL = f"postgresql://{DB_USER}:{DB_PASSWORD}@{DB_HOST}:{DB_PORT}/{DB_NAME}"

RRF_K = settings.rrf_k
SIMILARITY_THRESHOLD = settings.similarity_threshold
LEXICAL_RANK_THRESHOLD = settings.lexical_rank_threshold
CONFIDENCE_FILTER_ENABLED = settings.confidence_filter_enabled
CONFIDENCE_WEAK_VECTOR = settings.confidence_weak_vector
CONFIDENCE_WEAK_LEXICAL = settings.confidence_weak_lexical
CONFIDENCE_SPARSE_MARGIN = settings.confidence_sparse_margin
GRAPH_CONTEXT_ENABLED = settings.graph_context_enabled
GRAPH_SEED_VECTOR_THRESHOLD = settings.graph_seed_vector_threshold
GRAPH_SEED_LEXICAL_THRESHOLD = settings.graph_seed_lexical_threshold
GRAPH_CONTEXT_LIMIT = settings.graph_context_limit
RERANKER_ENABLED = settings.reranker_enabled
RERANKER_MODEL = settings.reranker_model
DOCUMENT_EXPANSION_ENABLED = settings.document_expansion_enabled
ONTOLOGY_INDEXING_ENABLED = settings.ontology_indexing_enabled
ONTOLOGY_SHADOW_ENABLED = settings.ontology_shadow_enabled
ONTOLOGY_CONTEXT_ENABLED = settings.ontology_context_enabled
ONTOLOGY_RANKING_ENABLED = settings.ontology_ranking_enabled
ONTOLOGY_HARD_RULES_ENABLED = settings.ontology_hard_rules_enabled

import contextvars
current_user_config = contextvars.ContextVar("current_user_config", default={})

REDIS_CACHE_HOST = settings.redis_cache_host
REDIS_CACHE_PORT = settings.redis_cache_port
REDIS_CACHE_PASSWORD = settings.redis_cache_password

REDIS_WIKI_HOST = settings.redis_wiki_host
REDIS_WIKI_PORT = settings.redis_wiki_port
REDIS_WIKI_PASSWORD = settings.redis_wiki_password

REDIS_STREAM_HOST = settings.redis_stream_host
REDIS_STREAM_PORT = settings.redis_stream_port
REDIS_STREAM_PASSWORD = settings.redis_stream_password
