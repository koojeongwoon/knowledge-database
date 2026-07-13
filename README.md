# LLM-Wiki: "나처럼 생각하는 AI"를 위한 멀티모달 개인/집단 지식베이스

> **"내가 쌓아온 고민과 대화 기록, 개발 환경 세팅의 의사결정을 그대로 지식화할 수 있다면? 사람이 바뀌어도 인수인계나 온보딩 없이 즉시 지식이 전수될 수 있다면? 그리고 같은 질문을 AI에게 반복하지 않아도 된다면?"**
>
> LLM-Wiki는 단순한 문서 데이터베이스 RAG 엔진이 아닙니다. 이 프로젝트는 **개인과 팀의 대화 흔적과 지식 파편들을 유기적으로 엮어, "나처럼 생각하고 나의 컨텍스트를 완벽히 이해하는 AI 비서"를 구축하기 위한 영속적 지식베이스 시스템**입니다.
>
> 텍스트 검색에 국한되지 않고 아키텍처 구성도, 디자인 스타일, 에러 로그 등 **시각적 자산(이미지)까지 텍스트와 하나의 페이지 번들로 묶어 완벽한 멀티모달 지식베이스**로 전환합니다.

---

## 1. 핵심 아키텍처 및 폴더 구조 (Page Bundle)

지식베이스 내의 텍스트와 이미지 자산은 **Page Bundle (글별 독립 폴더)** 구조로 격리하여 관리합니다.

* **디렉토리 예시**:
  ```text
  qa/                                             [Q&A 저널 폴더]
  └── 2026-06-21/
      └── 1430-kubernetes-setup/                  (Page Bundle: 글 단위 독립 폴더)
          ├── 1430-kubernetes-setup.md            (본문 마크다운 파일)
          └── assets/                             (자동 생성된 미디어 폴더)
              ├── architecture-diagram.png        (원본 이미지)
              └── architecture-diagram.png.md     (VLM이 생성한 이미지 사이드카 캐시)

  topics/                                         [주제별 노트 폴더 - 물리 범주화]
  ├── Development/                                (관심사 대범주 폴더)
  │   ├── llm-agent.md                            (최신 메인 노트)
  │   └── archive/                                (카테고리-레벨 과거 이력 보관)
  │       └── llm-agent_20260628_0154.md
  └── Finance/
      ├── 금리.md
      └── archive/
          └── 금리_20260629_1003.md
  ```
* **Obsidian 권장 설정**:
  * **새 첨부 파일을 생성할 위치**: `현재 폴더 아래의 하위 폴더` (하위 폴더 이름: `assets`)
  * **새로 생성하는 링크 형식**: `상대 경로`
  * **항상 업데이트되는 내부 링크**: `활성화`

---

## 2. RAG 검색 파이프라인 아키텍처

단순 벡터 유사도 검색을 넘어 **4단계의 고도화된 검색 품질 최적화 파이프라인**을 통해 최정밀 검색 결과를 제공합니다.

```
사용자 쿼리
    │
    ▼
[1단계: 하이브리드 검색]
    ├── pgvector 벡터 유사도 검색 (시맨틱 이해)
    └── PostgreSQL FTS 키워드 검색 (정확 매칭)
    │
    ▼
[2단계: RRF 점수 융합 (Reciprocal Rank Fusion)]
    └── 두 검색 결과를 순위 기반으로 병합 (K=60)
    │
    ▼
[3단계: Cross-Encoder 리랭킹 (BAAI/bge-reranker-v2-m3)]
    └── 쿼리-문서 관련성을 세밀하게 재평가하여 상위 노이즈 제거
    │
    ▼
[4단계: Graph 연관 확장]
    └── WikiLink 그래프를 따라 관련 Topic 문서를 자동으로 보강 추가
    │
    ▼
최종 검색 결과 (정밀도 최대화)
```

### 1) 문서 확장 (Document Expansion / Doc2Query) — 오프라인 임베딩 품질 극대화

검색 정확도를 근본적으로 끌어올리는 **오프라인 AI 보강 기법**입니다. 인덱싱 시점에 `gpt-4o-mini`를 호출하여 각 청크(Chunk)에 대한 **예상 사용자 질문 3개**와 **검색 키워드 5개(한/영 혼용)**를 자동 생성하고, 이를 임베딩 벡터 생성 입력 텍스트에 합쳐서 인덱싱합니다.

* **핵심 이점**:
  * **검색 지연 시간 0ms**: 모든 AI 연산이 오프라인(인덱싱) 시점에 처리되므로 사용자 쿼리 처리 속도에 영향 없음.
  * **한/영 언어 장벽 해소**: 영문으로만 작성된 기술 문서도 한글 자연어 질문으로 정확하게 검색 가능.
  * **의미 매칭률 극대화**: "질문형 쿼리" vs "서술형 문서" 간의 벡터 거리를 좁혀 Cosine Similarity를 극대화.
  * **증분 처리**: 새로 추가되거나 수정된 문서에만 LLM 호출이 발생하므로 API 비용이 극히 미미함.

* **데이터 투명성 보장**:
  * 생성된 예상 질문/키워드는 **오직 `embedding` 컬럼(벡터)에만 반영**됩니다.
  * 사용자 화면에 노출되거나 LLM에 전달되는 `content` 컬럼은 **원본 마크다운 그대로 유지**.

* **활성화 설정** (`configmap.yaml` 또는 `.env`):
  ```env
  DOCUMENT_EXPANSION_ENABLED=true
  ```

### 2) Cross-Encoder 리랭킹 (BAAI/bge-reranker-v2-m3)

벡터 검색(Bi-Encoder) 후 상위 N개 후보에 대해 **쿼리-문서 쌍을 동시에 입력으로 받아 세밀하게 관련성을 재평가**하는 Cross-Encoder 모델을 적용합니다.

* **모델**: `BAAI/bge-reranker-v2-m3` (다국어 지원, 한국어 포함)
* **Docker 이미지 빌드 시 사전 캐싱**: 컨테이너 Cold Start 없이 즉시 로드 가능
* **리소스**: 약 1.1GB~1.5GB RAM 사용 (K8s `limits.memory: 3Gi` 권장)
* **활성화 설정**:
  ```env
  RERANKER_ENABLED=true
  RERANKER_MODEL=BAAI/bge-reranker-v2-m3
  ```

---

## 3. 멀티테넌시 및 문서 공개 여부 (Visibility Control)

지식 문서마다 **공개(public) / 비공개(private)** 가시성을 지정할 수 있습니다. 지식을 커밋할 때 Markdown Frontmatter의 `visibility` 필드로 제어하며, 검색 시에도 자신의 비공개 문서와 모든 공개 문서만 반환됩니다.

### 1) Frontmatter 사용 예시

```yaml
---
title: "PostgreSQL 커넥션 풀 설정 노트"
description: "psycopg3 기반 커넥션 풀링 구현 기록"
tags: ["postgres", "psycopg3", "connection-pool"]
visibility: private   # private (본인만 조회) / public (전체 공개, 기본값)
---
```

* `public` (기본값): 모든 사용자가 검색 가능한 공개 지식
* `private`: 해당 문서를 커밋한 소유자(`owner_id`)만 검색 결과에서 조회 가능

### 2) 데이터베이스 접근 제어 쿼리 원리

```sql
-- 검색 시 자동 적용되는 가시성 필터
WHERE (visibility = 'public')
   OR (visibility = 'private' AND owner_id = :current_user_id)
```

### 3) `commit_new_knowledge` 도구에서 visibility 지정

```python
# MCP 도구 호출 시 가시성 명시
commit_new_knowledge(
    title="내 개인 설정 노트",
    content="...",
    visibility="private"  # 기본값: "private"
)
```

---

## 4. 텍스트 임베딩 및 DB 적재 프로세스 (PostgreSQL pgvector 단일 SSOT)

지식베이스의 모든 메타데이터와 관계 엣지는 **PostgreSQL(pgvector)** 단일 데이터베이스 내에서 단일 진실 공급원(SSOT)으로 통합 관리됩니다.

* **knowledge_documents**: 분할된 지식 청크와 벡터 임베딩(`VECTOR(1536)`), `owner_id`, `visibility` 저장.
* **knowledge_edges**: 문서 내 `[[WikiLink]]` 방향성 및 가중치(4대 신호 규칙) 관계선 관리.
* **knowledge_citations**: 각 지식 문서의 누적 인용 횟수 및 최종 인용 일자 관리.
* **knowledge_topics**: 지식 마크다운 카테고리별 매핑 및 저장 경로 관리.

---

## 5. 설치 및 시작 가이드

### 1) 로컬 환경 변수 설정 (`.env`)
`.env` 파일을 루트 디렉토리에 작성합니다.
```env
DB_HOST=localhost
DB_PORT=54320
DB_NAME=knowledge_db
DB_USER=postgres
DB_PASSWORD=postgres

# 임베딩 공급자: fake, openai, bge-m3
EMBEDDING_PROVIDER=openai
OPENAI_API_KEY=sk-proj-... # (텍스트 임베딩 생성 및 문서 확장 LLM 호출용)
EMBEDDING_DIM=1536

# 지식베이스(Obsidian Vault) 루트 경로
WIKI_DIR=.

# RAG 검색 파이프라인 설정
RERANKER_ENABLED=true
RERANKER_MODEL=BAAI/bge-reranker-v2-m3

# 문서 확장 (Doc2Query) 설정 - 인덱싱 시 예상 질문/키워드 자동 보강
DOCUMENT_EXPANSION_ENABLED=true
```

### 2) 가상환경 설정 및 의존성 설치 (`uv` 사용)
```bash
# 가상환경 생성 및 전체 패키지 동기화 설치
uv venv
uv pip install -e .
```

### 3) CLI 구동 및 명령어
* **전체 지식베이스 인덱싱 (문서 확장 임베딩 포함)**:
  ```bash
  python main.py index
  ```
* **하이브리드 시맨틱 검색 (리랭킹 적용)**:
  ```bash
  python main.py search "쿠버네티스 레이아웃 깨질 때" --limit 2
  ```

---

## 6. MCP 서버 연동 구성 (Stateless & Local-First)

### 1) 클라우드 원격 서버 배포 연동 (`mcp.json` 설정)
클라우드(OCI 등)에 백엔드 서버를 띄워두고, 로컬 PC에서 HTTPS Streamable HTTP 프로토콜로 직접 연동하는 표준 설정입니다.

```json
{
  "mcpServers": {
    "llm-wiki": {
      "type": "http",
      "url": "https://mcp.lynply.com/mcp",
      "headers": {
        "Authorization": "Bearer your-auth-token",
        "X-OpenAI-API-Key": "sk-proj-your-openai-api-key",
        "X-Storage-Type": "s3",
        "X-S3-Endpoint-URL": "https://<account-id>.r2.cloudflarestorage.com",
        "X-S3-Access-Key-ID": "your-r2-access-key-id",
        "X-S3-Secret-Access-Key": "your-r2-secret-access-key",
        "X-S3-Bucket-Name": "your-wiki-bucket-name"
      }
    }
  }
}
```

### 2) 로컬 단독 가동 방식 (Local Vault 직접 스캔)

```json
{
  "mcpServers": {
    "llm-wiki-local": {
      "command": "python",
      "args": ["/absolute/path/to/src/api/mcp_server.py"],
      "env": {
        "OPENAI_API_KEY": "sk-proj-your-openai-api-key",
        "STORAGE_TYPE": "local",
        "WIKI_DIR": "/absolute/path/to/your/obsidian/vault",
        "RERANKER_ENABLED": "true",
        "DOCUMENT_EXPANSION_ENABLED": "true"
      }
    }
  }
}
```

### 3) 제공 MCP 도구 (Tools)

| 도구명 | 설명 |
| :--- | :--- |
| `search_wiki_knowledge` | 자연어 쿼리로 지식베이스를 하이브리드 검색 (리랭킹 + 그래프 확장 포함) |
| `commit_new_knowledge` | 새 지식을 QA 저널(`qa/`)과 토픽(`topics/`)에 영속 저장. `visibility`로 공개 여부 지정 |
| `run_database_indexing` | 변경된 마크다운 파일을 감지하여 증분 인덱싱 실행 (문서 확장 포함) |

---

## 7. Kubernetes 배포 구성 (OCI / K3s)

### 1) ConfigMap 주요 설정 (`k8s/configmap.yaml`)

```yaml
apiVersion: v1
kind: ConfigMap
metadata:
  name: mcp-config
  namespace: llm-wiki
data:
  RERANKER_ENABLED: "true"
  RERANKER_MODEL: "BAAI/bge-reranker-v2-m3"
  DOCUMENT_EXPANSION_ENABLED: "true"
  REDIS_WIKI_HOST: "redis-wiki-cache-service.infra.svc.cluster.local"
  REDIS_WIKI_PORT: "6379"
  EMBEDDING_PROVIDER: "openai"
  STORAGE_TYPE: "s3"
```

### 2) 리소스 권장 사양 (`mcp-server` 컨테이너)

| 항목 | 권장값 | 비고 |
| :--- | :--- | :--- |
| `limits.memory` | `3Gi` | 리랭커 모델(~1.5GB) 상주 고려 |
| `limits.cpu` | `1.5` | 추론 연산 처리 |

---

## 8. 지식베이스 전용 Redis 캐시 및 감사 로깅

### 1) Redis 분산 캐시 구성 (K8s)
외부 인증 서버 호출 RTT 병목 해소를 위해 **지식베이스 전용 Redis 캐시 인스턴스**를 `infra` 네임스페이스 내에 독립적으로 구성합니다.

* `REDIS_WIKI_HOST`: Redis 서비스 명칭 (기본값: `redis-wiki-cache-service.infra.svc.cluster.local`)
* `REDIS_WIKI_PORT`: Redis 포트 (기본값: `6379`)
* **장애 복구 가드**: Redis 마비 시에도 외부 인증 API로 자동 우회 작동.

### 2) 감사 로그 (Audit Log) 사양

```json
{"timestamp": "2026-07-13T00:25:00Z", "level": "AUDIT", "user_id": "mcp_live_138b76...", "action": "AUTHENTICATE_BY_CACHE", "status": "SUCCESS"}
{"timestamp": "2026-07-13T00:25:05Z", "level": "AUDIT", "user_id": "mcp_live_138b76...", "action": "KNOWLEDGE_RETRIEVAL", "status": "SUCCESS", "payload": {"query": "Kubernetes Layout", "citations": ["qa/2026-07-12/1430-k8s.md"]}}
```

---

## 9. 코드 설계 및 주요 모듈 구성 (Bounded Context 수직 슬라이싱)

* **Shared Kernel & Infrastructure**:
  * [src/core/config.py](file:///Users/jw/__dev/knowledge/src/core/config.py): 전역 환경변수 로딩 (리랭커, 문서 확장, DB, 스토리지 설정 등).
  * [src/core/database/factory.py](file:///Users/jw/__dev/knowledge/src/core/database/factory.py): DB 팩토리 매니저.
  * [src/core/database/connection.py](file:///Users/jw/__dev/knowledge/src/core/database/connection.py): psycopg3 기반 스레드 안전 커넥션 풀 관리.
  * [src/core/storage/factory.py](file:///Users/jw/__dev/knowledge/src/core/storage/factory.py): Local/S3(R2) 스토리지 추상화 팩토리.
* **Indexing Bounded Context**:
  * [src/indexing/application/service.py](file:///Users/jw/__dev/knowledge/src/indexing/application/service.py): 증분 인덱싱 파이프라인 + **Document Expansion(Doc2Query) 오프라인 보강**.
  * [src/indexing/domain/model.py](file:///Users/jw/__dev/knowledge/src/indexing/domain/model.py): `Chunk`(VO), `Edge`(4대 신호 가중치) 도메인 모델.
  * [src/indexing/infrastructure/indexing_repository.py](file:///Users/jw/__dev/knowledge/src/indexing/infrastructure/indexing_repository.py): PostgreSQL pgvector 인덱싱 DDL/DML.
* **Retrieval Bounded Context**:
  * [src/retrieval/application/service.py](file:///Users/jw/__dev/knowledge/src/retrieval/application/service.py): 하이브리드 검색 → RRF → **Cross-Encoder 리랭킹** → 그래프 확장 오케스트레이션.
  * [src/retrieval/infrastructure/retrieval_repository.py](file:///Users/jw/__dev/knowledge/src/retrieval/infrastructure/retrieval_repository.py): pgvector 벡터/FTS 쿼리 + **visibility 접근 제어** 필터 적용.
* **API & Interfaces**:
  * [src/api/mcp_server.py](file:///Users/jw/__dev/knowledge/src/api/mcp_server.py): FastMCP 서버 선언 (`search_wiki_knowledge`, `commit_new_knowledge`, `run_database_indexing`).
  * [src/api/middleware.py](file:///Users/jw/__dev/knowledge/src/api/middleware.py): Redis 캐시 기반 토큰 인증 + SSL 오프로딩 + 멀티테넌트 ContextVar 주입.

---

## 10. 에이전트 연동 및 자동 지식화 규칙 (Agent Integration)

본 저장소는 AI 코딩 에이전트(Antigravity 등)가 유기적으로 지식을 습득·활용할 수 있도록 [.agents/AGENTS.md](file:///Users/jw/__dev/knowledge/.agents/AGENTS.md) 규칙을 제공합니다.

### 핵심 에이전트 행동 지침

* **지식 RAG 우선 검색**: 모든 질문에 답변하기 전 무조건 `search_wiki_knowledge`를 먼저 호출하여 관련 컨텍스트를 확보.
* **비동기 백그라운드 지식화**: `commit_new_knowledge` 및 `run_database_indexing`은 서브에이전트를 통해 비동기 처리하여 사용자 대기 시간 최소화.
* **세션 아티팩트 이관 동의**: 대화 중 생성된 설계서/보고서 등의 아티팩트는 사용자 동의 후 `resource_paths`에 추가하여 R2 스토리지로 이관.
* **visibility 자동화**: QA 저널은 기본 `private`, Topics 토픽은 기본 `public`으로 자동 지정하며 사용자가 별도 지정하지 않아도 됨.
