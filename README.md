# LLM-Wiki

LLM-Wiki는 Markdown 지식과 이미지 자산을 S3/R2에 보관하고, PostgreSQL·pgvector에 인덱싱해 MCP로 검색·기록하는 멀티테넌트 지식베이스입니다. 사용자별 OpenAI API 키와 스토리지 설정을 사용하며 공개 문서와 본인 소유 비공개 문서를 함께 검색합니다.

## 핵심 동작

- 원본 저장소: 사용자별 S3 또는 R2. 로컬 파일 저장소 fallback은 없습니다.
- 검색 인덱스: PostgreSQL FTS와 pgvector.
- 검색 순위: 벡터·키워드 후보를 문서 단위 weighted RRF로 융합합니다.
- 무관 결과 차단: vector/lexical 신호와 상위 결과 margin을 사용하는 confidence 필터를 적용합니다.
- 그래프 검색: 강한 직접 검색 결과만 seed로 사용하고, 연관 문서는 직접 결과 순위에 섞지 않고 `graph_context`로 분리합니다.
- 리랭커: 현재 운영 설정에서는 비활성화되어 있습니다.
- 사용자 피드백: 모든 검색에 `Search Event ID`를 발급하고 정답·오답·no-answer 라벨을 수집합니다.
- 스키마 관리: 애플리케이션 자체 버전 마이그레이션을 서버 시작 시 자동 적용하며 CLI에서도 실행할 수 있습니다.

## Page Bundle 구조

Markdown 본문과 첨부 자산은 글 단위 폴더로 묶습니다.

```text
qa/
└── 2026-07-16/
    └── 1430-kubernetes-setup/
        ├── 1430-kubernetes-setup.md
        └── assets/
            ├── architecture.png
            └── architecture.png.md

topics/
└── Development/
    ├── llm-wiki.md
    └── archive/
        └── llm-wiki_20260716_1430.md
```

Obsidian에서는 새 첨부 파일 위치를 현재 폴더 아래 `assets`, 링크 형식을 상대 경로로 설정하는 것을 권장합니다.

## 검색 파이프라인

```text
질의
  ├─ pgvector semantic 후보
  └─ PostgreSQL FTS lexical 후보
          ↓
  문서 단위 weighted RRF fusion
          ↓
  confidence / no-answer 판정
          ↓
  직접 검색 결과
          └─ 강한 seed가 있을 때만 graph_context 보강
```

검색 결과에는 신호별 의미가 섞이지 않도록 vector similarity, lexical rank, RRF score, citation count를 구분해 표시합니다. 인용 횟수는 동률 보조 신호이며 관련성 임계치를 통과시키는 근거로 사용하지 않습니다.

현재 K8s 설정의 주요 값은 다음과 같습니다.

```env
RRF_K=60
SIMILARITY_THRESHOLD=0.35
LEXICAL_RANK_THRESHOLD=0.02
CONFIDENCE_FILTER_ENABLED=true
CONFIDENCE_WEAK_VECTOR=0.38
CONFIDENCE_WEAK_LEXICAL=0.015
CONFIDENCE_SPARSE_MARGIN=0.5
GRAPH_CONTEXT_ENABLED=true
GRAPH_SEED_VECTOR_THRESHOLD=0.5
GRAPH_SEED_LEXICAL_THRESHOLD=0.05
GRAPH_CONTEXT_LIMIT=2
RERANKER_ENABLED=false
```

`DOCUMENT_EXPANSION_ENABLED=true`이면 인덱싱 시 예상 질문과 검색 키워드를 생성해 임베딩 입력만 보강합니다. 원본 `content`는 변경하지 않습니다.

## 멀티테넌시와 공개 범위

문서에는 `owner_id`와 `visibility`가 저장됩니다.

```yaml
---
title: PostgreSQL 연결 설정
description: 운영 연결 구성 기록
tags: [postgresql, operations]
visibility: private
---
```

- `private`: 소유자만 검색할 수 있습니다. `commit_new_knowledge`의 기본값입니다.
- `public`: 모든 인증 사용자가 검색할 수 있습니다.
- 검색 조건: 공개 문서 또는 현재 `owner_id`의 비공개 문서만 반환합니다.
- `qa/`와 `topics/`라는 경로만으로 공개 범위가 자동 결정되지는 않습니다. Frontmatter와 저장된 DB 값을 기준으로 합니다.

## 사용자별 OpenAI·S3/R2 설정

`https://knowledge.lynply.com/settings`에서 로그인한 사용자의 설정을 저장합니다.

- OpenAI API 키
- 스토리지 종류: `s3` 또는 `r2`
- S3 호환 endpoint와 bucket
- access key ID와 secret access key

비밀 값은 `SETTINGS_ENCRYPTION_KEY`로 암호화되어 `knowledge_user_settings`에 저장되고 조회 API에는 원문이 반환되지 않습니다. 설정을 저장하면 해당 사용자의 프로세스 내 설정 캐시를 즉시 무효화하므로 다음 요청에서 DB 값을 다시 읽습니다. 이후 요청은 다시 캐시를 사용합니다.

S3/R2 설정은 필수입니다. 사용자 설정이 없거나 endpoint/bucket/자격 증명이 불완전하면 로컬 디스크로 우회하지 않고 오류를 반환합니다. 서버 전역 OpenAI/S3 비밀 환경변수로 사용자 설정을 대신하지 않습니다.

로그인 흐름은 다음과 같습니다.

```text
/settings → 외부 인증 포털 → /callback → POST /api/session
          → Secure/HttpOnly/SameSite=Lax 세션 쿠키 → /settings
```

## 데이터베이스 마이그레이션

Alembic이나 SQLAlchemy migration을 사용하지 않습니다. `src/core/database/migrations.py`의 경량 버전 마이그레이션을 psycopg 트랜잭션과 PostgreSQL advisory lock으로 실행합니다. 적용 이력은 `knowledge_schema_migrations`에 저장됩니다.

서버 시작 시 미적용 버전이 자동 적용됩니다. 운영자가 명시적으로 실행할 수도 있습니다.

```bash
uv run python main.py migrate
```

현재 마이그레이션은 1~8입니다.

| 버전 | 내용 |
| --- | --- |
| 1 | 문서, 엣지, 인용, 토픽, 감사 로그, 사용자, API 키 코어 스키마 |
| 2 | `owner_id`·`visibility` 멀티테넌시 제약조건 |
| 3 | DB 기반 인덱싱 재시도 큐 |
| 4 | 암호화된 사용자 OpenAI·S3/R2 설정 |
| 5 | pgvector HNSW 및 PostgreSQL FTS 인덱스 |
| 6 | 원격 사용자 저장소 강제 전환 이력 |
| 7 | 기본 저장소를 S3로 확정 |
| 8 | 검색 이벤트 및 human-labeled 피드백 |

주요 테이블은 `knowledge_documents`, `knowledge_edges`, `knowledge_citations`, `knowledge_topics`, `knowledge_indexing_jobs`, `knowledge_user_settings`, `knowledge_search_events`, `knowledge_search_feedback`입니다.

## 설치와 로컬 CLI

개발 의존성을 포함해 동기화합니다. `pytest`도 uv 프로젝트 의존성에 포함되어 있습니다.

```bash
uv sync --dev
uv run pytest
```

DB, 암호화 키, Redis 등 서버 인프라 설정은 `.env` 또는 K8s Secret/ConfigMap으로 주입합니다. 실제 OpenAI/S3 키는 `.env`나 Kubernetes manifest에 넣지 않고 설정 페이지에서 사용자별로 저장합니다.

```env
DB_HOST=localhost
DB_PORT=5432
DB_NAME=knowledge_db
DB_USER=postgres
DB_PASSWORD=postgres
SETTINGS_ENCRYPTION_KEY=replace-with-a-long-random-secret
EMBEDDING_PROVIDER=openai
EMBEDDING_DIM=1536
```

인덱싱과 검색은 사용할 DB 설정의 소유자를 반드시 지정합니다.

```bash
uv run python main.py index --owner-id USER_ID
uv run python main.py search "쿠버네티스 배포 절차" --limit 5 --owner-id USER_ID
uv run python main.py retry-indexing --limit 100
uv run python main.py retry-indexing --limit 100 --force
```

`retry-indexing`은 스케줄러/CronJob용 운영 명령입니다. `commit_new_knowledge`는 실제로 쓴 파일을 저장하고 DB 큐에 등록한 뒤 즉시 반환하며, 인덱싱은 스케줄러가 비동기로 처리합니다.

## MCP와 웹 API

MCP 클라이언트는 인증 API 키를 Bearer token으로 전달합니다.

```json
{
  "mcpServers": {
    "llm-wiki": {
      "type": "http",
      "url": "https://mcp.lynply.com/mcp",
      "headers": {
        "Authorization": "Bearer YOUR_KNOWLEDGE_API_KEY"
      }
    }
  }
}
```

제공 MCP 도구:

| 도구 | 용도 |
| --- | --- |
| `search_wiki_knowledge` | 하이브리드 검색. 응답에 `Search Event ID` 포함 |
| `submit_search_feedback` | 검색 결과의 relevant/irrelevant/no-answer/missing-answer 라벨 저장 |
| `commit_new_knowledge` | 지식 기록 후 변경 파일의 비동기 인덱싱 등록 |
| `run_database_indexing` | 외부 수정 파일을 즉시 반영하는 지정 증분 인덱싱 |

주요 웹 endpoint:

| Method | Path | 용도 |
| --- | --- | --- |
| GET | `/health` | 상태 확인 |
| GET | `/settings` | 사용자 설정 및 최근 검색 검수 UI |
| GET, PUT | `/api/settings` | 사용자별 OpenAI·S3/R2 설정 조회·저장 |
| GET, POST, DELETE | `/api/keys` | Knowledge API key 관리 |
| GET | `/api/search-feedback/events` | 최근 검색 이벤트와 라벨 조회 |
| PUT | `/api/search-feedback/{search_id}` | human-labeled 피드백 저장 |
| POST | `/mcp` | Streamable HTTP MCP |

검색 질의는 이벤트 저장 전에 민감정보 패턴을 마스킹합니다. 피드백은 검색 당시 반환 문서 snapshot과 함께 소유자 범위로 저장됩니다.

## 검색 품질 평가

개발 회귀 세트와 정답을 숨긴 blind holdout을 분리합니다. 평가 규칙과 게이트는 `docs/search-quality-blind-evaluation.md`, `tests/search_quality_development.json`, `tests/search_quality_gates.json`을 참고합니다.

```bash
uv run python main.py evaluate-search \
  --owner-id USER_ID \
  --cases tests/search_quality_development.json \
  --limit 5

uv run python main.py run-blind-search \
  --owner-id USER_ID \
  --queries tests/search_quality_blind_queries.json \
  --output tests/search_quality_blind_predictions.json

uv run python main.py score-blind-search \
  --queries tests/search_quality_blind_queries.json \
  --predictions tests/search_quality_blind_predictions.json \
  --answers tests/search_quality_blind_answers.json
```

Blind 파일과 결과는 Git ignore 대상입니다. 최근 Blind-v2 결과는 Top-1 `0.55`, Recall@5 `0.875`, MRR `0.6842`, no-answer precision/recall `0.90/0.90`으로 no-answer 게이트는 통과했지만 전체 품질 게이트는 아직 통과하지 못했습니다. 따라서 현재 검색 품질을 완료 상태로 간주하지 않습니다.

## Kubernetes 배포

배포 파일은 `k8s/`에 있습니다.

- `k8s/configmap.yaml`: 비밀이 아닌 검색·Redis·서비스 설정
- `k8s/mcp-deployment.yaml`: MCP/설정 웹 애플리케이션
- `k8s/mcp-secrets.yaml.example`: 실제 값이 없는 Secret key 구조 예시

실제 Secret manifest, `.env`, 감사 로그와 회전 로그는 커밋하지 않습니다. 예시 파일에는 필요한 key 이름만 남기고 값은 비워 둡니다.

## 주요 코드 위치

- `src/core/database/migrations.py`: 버전 스키마 마이그레이션
- `src/core/storage/factory.py`: 사용자별 S3/R2 저장소 생성
- `src/settings/service.py`: 암호화 설정 저장과 캐시 무효화
- `src/settings/web.py`: 로그인 세션, 설정, API key, 검색 피드백 웹 API
- `src/indexing/application/service.py`: 변경 파일 중심 증분 인덱싱
- `src/indexing/infrastructure/job_repository.py`: DB 재시도 큐
- `src/retrieval/application/service.py`: fusion, confidence, graph 보조 검색
- `src/retrieval/feedback.py`: 검색 이벤트와 human label 저장
- `src/retrieval/evaluation.py`: 개발·blind 품질 평가
- `src/api/mcp_server.py`: MCP 도구와 웹/MCP 라우팅

## 에이전트 사용 원칙

저장소의 `.agents/AGENTS.md`는 다음 흐름을 요구합니다.

1. 개인 지식이 필요한 요청은 먼저 `search_wiki_knowledge`로 검색합니다.
2. 새 지식이나 업무 규칙은 `commit_new_knowledge`로 기록합니다.
3. `commit_new_knowledge`가 비동기 인덱싱 작업을 등록하므로, 외부 수정 파일을 즉시 반영할 때만 `run_database_indexing(file_paths=[...])`을 사용합니다.
