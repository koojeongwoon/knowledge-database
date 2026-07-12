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

## 2. 텍스트 임베딩 및 DB 적재 프로세스 (PostgreSQL pgvector 단일 SSOT)

지식베이스의 모든 메타데이터와 관계 엣지는 오프라인 Drift 위험이 있는 로컬 파일들을 탈피하여 **PostgreSQL(pgvector)** 단일 데이터베이스 내에서 단일 진실 공급원(SSOT)으로 통합 관리됩니다.

* **knowledge_documents**: 분할된 지식 청크와 벡터 임베딩(`VECTOR(1536)`) 저장.
* **knowledge_edges**: 문서 내 `[[WikiLink]]` 방향성 및 가중치(4대 신호 규칙) 관계선 관리.
* **knowledge_citations**: 각 지식 문서의 누적 인용 횟수 및 최종 인용 일자 관리.
* **knowledge_topics**: 지식 마크다운 카테고리별 매핑 및 저장 경로 관리.

---

## 3. 설치 및 시작 가이드

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
OPENAI_API_KEY=sk-proj-... # (텍스트 임베딩 생성용으로 사용됩니다)
EMBEDDING_DIM=1536

# 지식베이스(Obsidian Vault) 루트 경로
WIKI_DIR=.
```

### 2) 가상환경 설정 및 의존성 설치 (`uv` 사용)
```bash
# 가상환경 생성 및 전체 패키지 동기화 설치
uv venv
uv pip install -e .
```

### 3) CLI 구동 및 명령어
* **전체 지식베이스 인덱싱 (텍스트 & 이미지 일괄 증분 반영)**:
  ```bash
  python main.py index
  ```
* **하이브리드 시맨틱 검색**:
  ```bash
  python main.py search "쿠버네티스 레이아웃 깨질 때" --limit 2
  ```

---

## 4. MCP 서버 연동 구성 (Stateless & Local-First)

### 1) 클라우드 원격 서버 배포 연동 (`mcp.json` 설정)
클라우드(OCI 등)에 백엔드 서버를 띄워두고, 로컬 PC에 Node.js(`npx`)의 설치 없이 직접 HTTPS 및 Streamable HTTP 프로토콜로 다이렉트 연동을 맺는 표준 명세 설정입니다.

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
소스를 로컬 호스트 내부에서 단독 구동하고 싶을 때 사용합니다.

```json
{
  "mcpServers": {
    "llm-wiki-local": {
      "command": "python",
      "args": ["/absolute/path/to/src/api/mcp_server.py"],
      "env": {
        "OPENAI_API_KEY": "sk-proj-your-openai-api-key",
        "STORAGE_TYPE": "local",
        "WIKI_DIR": "/absolute/path/to/your/obsidian/vault"
      }
    }
  }
}
```

---

## 5. 지식베이스 전용 Redis 캐시 및 감사 로깅

### 1) Redis 분산 캐시 구성 (K8s)
지식베이스 서버는 매번 발생하는 외부 인증 서버 호출 RTT 병목을 해소하기 위해 **지식베이스 전용 Redis 캐시 인스턴스(`redis-wiki-cache`)**를 `infra` 네임스페이스 내에 독립적으로 구성하여 연동합니다.

* **인프라 환경 변수**:
  * `REDIS_WIKI_HOST`: Redis 서비스 명칭 (기본값: `redis-wiki-cache-service.infra.svc.cluster.local`)
  * `REDIS_WIKI_PORT`: Redis 포트 (기본값: `6379`)
* **보안 자격 증명**:
  * 배포 시 공용 Secret(`db-secrets`)의 `redis-password` 값을 주입받아 인증을 맺습니다.
* **장애 복구 가드 (Design for Failure)**:
  * Redis가 마비되거나 통신 에러가 터지더라도 서버는 1초 이내에 예외를 캡슐화 처리(삼킴)하고, 외부 인증 실시간 API 호출로 안전하게 우회 작동합니다.

## 4. 코드 설계 및 주요 모듈 구성 (Bounded Context 수직 슬라이싱)

### 1) 백엔드 핵심 코드 파일 및 패키지 구조
* [pyproject.toml](file:///Users/jw/__dev/knowledge/pyproject.toml): `hatchling` 및 `uv` 표준을 따르는 의존성 설정 파일.
* **Shared Kernel & Infrastructure**:
  * [src/core/database/factory.py](file:///Users/jw/__dev/knowledge/src/core/database/factory.py): DB 팩토리 매니저.
  * [src/core/database/postgres.py](file:///Users/jw/__dev/knowledge/src/core/database/postgres.py): PostgreSQL pgvector 커넥션 생명주기 관리.
  * [src/core/cache/factory.py](file:///Users/jw/__dev/knowledge/src/core/cache/factory.py): 스프링 DI 철학을 담은 캐시 추상화 팩토리.
  * [src/core/cache/redis.py](file:///Users/jw/__dev/knowledge/src/core/cache/redis.py): RedisTemplate 스타일의 캐시 및 예외 은닉 구현체.
* **Wiki Bounded Context**:
  * [src/wiki/domain/parser.py](file:///Users/jw/__dev/knowledge/src/wiki/domain/parser.py): 마크다운 구조 분석, YAML Frontmatter 추출 및 위키링크 파싱.
* **Indexing Bounded Context**:
  * [src/indexing/domain/model.py](file:///Users/jw/__dev/knowledge/src/indexing/domain/model.py): Chunk(VO), Edge(Entity: 4대 신호 가중치 계산) 도메인 모델.
  * [src/indexing/domain/repository.py](file:///Users/jw/__dev/knowledge/src/indexing/domain/repository.py): 인덱싱 추상 인터페이스 계약 정의.
  * [src/indexing/application/service.py](file:///Users/jw/__dev/knowledge/src/indexing/application/service.py): 로컬-DB 비교 증분 인덱싱 파이프라인 제어 (병렬 처리 지원).
  * [src/indexing/infrastructure/postgres.py](file:///Users/jw/__dev/knowledge/src/indexing/infrastructure/postgres.py): PostgreSQL 인덱싱 및 토픽 정보 DDL/DML 영속화 처리.
* **Retrieval Bounded Context**:
  * [src/retrieval/domain/repository.py](file:///Users/jw/__dev/knowledge/src/retrieval/domain/repository.py): 검색 조회 추상 인터페이스 계약 정의.
  * [src/retrieval/application/service.py](file:///Users/jw/__dev/knowledge/src/retrieval/application/service.py): 하이브리드 검색, RRF 결합, 리랭커 및 그래프 연관 확장 오케스트레이션.
  * [src/retrieval/infrastructure/postgres.py](file:///Users/jw/__dev/knowledge/src/retrieval/infrastructure/postgres.py): pgvector 벡터 및 FTS 키워드 DB 검색 쿼리 실행.
* **API & Interfaces (단일 책임 격리)**:
  * [src/api/mcp_server.py](file:///Users/jw/__dev/knowledge/src/api/mcp_server.py): FastMCP 서버 선언 및 구동 엔트리포인트.
  * [src/api/middleware.py](file:///Users/jw/__dev/knowledge/src/api/middleware.py): 공용 Redis 캐시 연동 토큰 검증 및 SSL 오프로딩 인증 필터.
  * [src/api/agent_tool.py](file:///Users/jw/__dev/knowledge/src/api/agent_tool.py): 비즈니스 도구 구현 전용 경량 모듈.
  * [src/api/dto.py](file:///Users/jw/__dev/knowledge/src/api/dto.py) / [exceptions.py](file:///Users/jw/__dev/knowledge/src/api/exceptions.py) / [decorators.py](file:///Users/jw/__dev/knowledge/src/api/decorators.py): 응답 데이터, 비즈니스 에러, 에러 처리 데코레이터 전용 격리 파일들.

### 2) 감사 로그 (Audit Log) 사양
모든 API Key 인증 상태 및 지식 생성/조회 이력은 정형화된 `[AUDIT]` 머리글을 가진 단일 JSON 라인 로그로 출력되어 수집됩니다.

* **로그 예시 (인증 성공)**:
  ```json
  {"timestamp": "2026-07-13T00:25:00Z", "level": "AUDIT", "user_id": "mcp_live_138b76...", "action": "AUTHENTICATE_BY_CACHE", "status": "SUCCESS"}
  ```
* **로그 예시 (검색 성공)**:
  ```json
  {"timestamp": "2026-07-13T00:25:05Z", "level": "AUDIT", "user_id": "mcp_live_138b76...", "action": "KNOWLEDGE_RETRIEVAL", "status": "SUCCESS", "payload": {"query": "Kubernetes Layout", "limit": 5, "citations": ["qa/2026-07-12/1430-k8s.md"]}}
  ```
