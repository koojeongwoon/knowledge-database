import os
from pathlib import Path
from typing import Optional
from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Base directory of the knowledge base project
BASE_DIR = Path(__file__).resolve().parent.parent.parent

class Settings(BaseSettings):
    # 지식베이스(Obsidian Vault) 루트 디렉토리 설정
    wiki_dir: str = Field(default=".", validation_alias="WIKI_DIR")

    # 스토리지 유형 설정 (local, s3)
    storage_type: str = Field(default="local", validation_alias="STORAGE_TYPE")
    s3_endpoint_url: str = Field(default="", validation_alias="S3_ENDPOINT_URL")
    s3_access_key_id: str = Field(default="", validation_alias="S3_ACCESS_KEY_ID")
    s3_secret_access_key: str = Field(default="", validation_alias="S3_SECRET_ACCESS_KEY")
    s3_bucket_name: str = Field(default="", validation_alias="S3_BUCKET_NAME")

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

    # Cross-Encoder 리랭커 설정 (비활성 시 RRF 결과를 그대로 사용)
    reranker_enabled: bool = Field(default=False, validation_alias="RERANKER_ENABLED")
    reranker_model: str = Field(default="BAAI/bge-reranker-v2-m3", validation_alias="RERANKER_MODEL")

    # 문서 확장 (Document Expansion) 설정
    document_expansion_enabled: bool = Field(default=False, validation_alias="DOCUMENT_EXPANSION_ENABLED")

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
WIKI_DIR = os.path.abspath(settings.wiki_dir)
STORAGE_TYPE = settings.storage_type.lower()
S3_ENDPOINT_URL = settings.s3_endpoint_url
S3_ACCESS_KEY_ID = settings.s3_access_key_id
S3_SECRET_ACCESS_KEY = settings.s3_secret_access_key
S3_BUCKET_NAME = settings.s3_bucket_name

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
RERANKER_ENABLED = settings.reranker_enabled
RERANKER_MODEL = settings.reranker_model
DOCUMENT_EXPANSION_ENABLED = settings.document_expansion_enabled

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

