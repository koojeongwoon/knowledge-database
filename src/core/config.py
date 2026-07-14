import os

from dotenv import load_dotenv

# .env 파일 로드 (시스템 환경 변수가 있더라도 .env 파일 설정으로 덮어씀)
load_dotenv(override=True)

# 지식베이스(Obsidian Vault) 루트 디렉토리 설정 (기본값: 현재 디렉토리)
WIKI_DIR = os.path.abspath(os.getenv("WIKI_DIR", "."))

# 스토리지 유형 설정 (local, s3)
STORAGE_TYPE = os.getenv("STORAGE_TYPE", "local").lower()
S3_ENDPOINT_URL = os.getenv("S3_ENDPOINT_URL", "")
S3_ACCESS_KEY_ID = os.getenv("S3_ACCESS_KEY_ID", "")
S3_SECRET_ACCESS_KEY = os.getenv("S3_SECRET_ACCESS_KEY", "")
S3_BUCKET_NAME = os.getenv("S3_BUCKET_NAME", "")

DB_TYPE = os.getenv("DB_TYPE", "postgres").lower()
DB_HOST = os.getenv("DB_HOST", "localhost")
DB_PORT = int(os.getenv("DB_PORT", "5432"))
DB_NAME = os.getenv("DB_NAME", "knowledge_db")
DB_USER = os.getenv("DB_USER", "postgres")
DB_PASSWORD = os.getenv("DB_PASSWORD", "postgres")
DB_MIN_CONNECTIONS = int(os.getenv("DB_MIN_CONNECTIONS", "2"))
DB_MAX_CONNECTIONS = int(os.getenv("DB_MAX_CONNECTIONS", "20"))

# 임베딩 공급자 설정 (fake, openai, bge-m3)
EMBEDDING_PROVIDER = os.getenv("EMBEDDING_PROVIDER", "fake").lower()

# 임베딩 차원 설정
# bge-m3는 1024 고정이며, 그 외 모델은 설정값 혹은 기본 1536 사용
if EMBEDDING_PROVIDER == "bge-m3":
    EMBEDDING_DIM = 1024
else:
    EMBEDDING_DIM = int(os.getenv("EMBEDDING_DIM", "1536"))

# DB Connection URI 생성
DATABASE_URL = f"postgresql://{DB_USER}:{DB_PASSWORD}@{DB_HOST}:{DB_PORT}/{DB_NAME}"

# ── Retrieval Pipeline 설정 ──────────────────────────────────────────
# RRF (Reciprocal Rank Fusion) 파라미터
RRF_K = int(os.getenv("RRF_K", "60"))

# 최종 검색 결과 유사도 임계치 (이 점수 미만의 결과는 제외)
# - 리랭커 미사용 시: 정규화된 RRF 점수(0~1) 기준
# - 리랭커 사용 시: sigmoid 출력(0~1) 기준
SIMILARITY_THRESHOLD = float(os.getenv("SIMILARITY_THRESHOLD", "0.35"))

# Cross-Encoder 리랭커 설정 (비활성 시 RRF 결과를 그대로 사용)
RERANKER_ENABLED = os.getenv("RERANKER_ENABLED", "false").lower() == "true"
RERANKER_MODEL = os.getenv("RERANKER_MODEL", "BAAI/bge-reranker-v2-m3")

# 문서 확장 (Document Expansion) 설정 (인덱싱 시 예상 질문/키워드 자동 추출 임베딩)
DOCUMENT_EXPANSION_ENABLED = os.getenv("DOCUMENT_EXPANSION_ENABLED", "false").lower() == "true"

import contextvars
current_user_config = contextvars.ContextVar("current_user_config", default={})

# ── Redis 설정 ────────────────────────────────────────────────────────
# Redis Cache (세션 공유, 임시 캐싱) — allkeys-lru / AOF off
REDIS_CACHE_HOST = os.getenv("REDIS_CACHE_HOST", "redis-cache-service.infra.svc.cluster.local")
REDIS_CACHE_PORT = int(os.getenv("REDIS_CACHE_PORT", "6379"))
REDIS_CACHE_PASSWORD = os.getenv("REDIS_CACHE_PASSWORD", "")

# Redis Wiki Cache (지식베이스 전용 토큰 캐싱) — allkeys-lru / AOF off
REDIS_WIKI_HOST = os.getenv("REDIS_WIKI_HOST", "redis-wiki-cache-service.infra.svc.cluster.local")
REDIS_WIKI_PORT = int(os.getenv("REDIS_WIKI_PORT", "6379"))
REDIS_WIKI_PASSWORD = os.getenv("REDIS_WIKI_PASSWORD", "")

# Redis Stream (비동기 아웃박스 이벤트 큐) — noeviction / AOF on
REDIS_STREAM_HOST = os.getenv("REDIS_STREAM_HOST", "redis-stream-service.infra.svc.cluster.local")
REDIS_STREAM_PORT = int(os.getenv("REDIS_STREAM_PORT", "6380"))
REDIS_STREAM_PASSWORD = os.getenv("REDIS_STREAM_PASSWORD", "")
