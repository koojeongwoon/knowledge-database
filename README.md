# LLM-Wiki

LLM-Wiki는 Markdown 지식과 첨부 자료를 사용자별 S3/R2에 저장하고, PostgreSQL FTS와 pgvector로 검색하며, MCP를 통해 검색·기록·학습·업무 기준본 관리를 제공하는 멀티테넌트 지식베이스입니다.

이 문서는 처음 연결하는 사용자부터 운영자까지 실제로 사용할 수 있도록 다음 내용을 설명합니다.

- 설치, 설정, 서버 실행, MCP 연결
- 일반 지식 검색과 기록
- Inbox 자료 수집
- 학습 세션, 복습, 학습 결과의 승인 기반 지식화
- 업무 기준본의 준비, 확정, 사용, 후속 버전 생성
- 검색 피드백과 인덱싱 운영
- 전체 MCP 도구 23개의 입력 계약
- 웹 화면·API·CLI·마이그레이션·보안 경계

## 가장 중요한 사용 원칙

1. 일반 지식은 계속 최신화되는 live corpus입니다.
2. 업무 기준본은 사용자가 명시적으로 선택하고 확정한 불변 snapshot입니다.
3. 기준본 초안 준비, 확정, 검색 적용은 각각 별도의 명시적 요청이 있어야 합니다.
4. 기준본의 새 버전을 확정해도 기존 업무가 사용하는 버전은 자동으로 바뀌지 않습니다.
5. 기준본 검색 결과가 없어도 일반 지식으로 자동 fallback하지 않습니다.
6. Inbox 자료는 기본적으로 `unverified`이며 자동으로 Knowledge가 되지 않습니다.
7. 학습 결과도 후보 준비 → staging → 개별 승인 → commit 단계를 거쳐야 Knowledge가 됩니다.
8. 클릭·열기·검색 행동은 정답 라벨이 아닙니다. 검색 품질에는 사용자가 명시적으로 제출한 평가만 사용합니다.

## 시스템 구성

```text
MCP client / Browser
        │
        ├─ mcp.lynply.com/mcp ── MCPAuthMiddleware ── 23 MCP tools
        │
        └─ knowledge.lynply.com ── OAuth session ── Settings Web
                                             │
                  ┌──────────────────────────┴──────────────────────────┐
                  │                                                     │
          사용자별 S3/R2                                         PostgreSQL
      qa/, topics/, inbox/                              documents, vectors, jobs,
      baseline-drafts/, baselines/                      learning, feedback, baselines
```

- 원본 저장소: 사용자별 S3 또는 R2. 로컬 fallback은 없습니다.
- 검색 인덱스: PostgreSQL FTS와 pgvector.
- 소유권: 인증으로 결정된 `owner_id`를 서버에서 강제합니다.
- 공개 범위: `public` 또는 현재 사용자의 `private` 문서만 일반 검색합니다.
- 기준본: 별도 디렉터리와 별도 DB 테이블을 사용하며 일반 검색에서 제외합니다.

## 빠른 시작

### 1. 의존성 설치

Python 3.10 이상과 `uv`를 사용합니다.

```bash
uv sync --dev
uv run pytest
```

### 2. 서버 인프라 환경변수 설정

DB, Redis, 암호화 키 같은 서버 인프라 설정은 `.env` 또는 Kubernetes Secret/ConfigMap으로 주입합니다.

```env
DB_HOST=localhost
DB_PORT=54320
DB_NAME=knowledge_db
DB_USER=postgres
DB_PASSWORD=postgres

SETTINGS_ENCRYPTION_KEY=replace-with-a-long-random-secret
EMBEDDING_PROVIDER=openai
EMBEDDING_DIM=1536

SETTINGS_PUBLIC_HOST=knowledge.lynply.com
MCP_PUBLIC_HOST=mcp.lynply.com
```

실제 사용자 OpenAI/S3 자격 증명은 서버 공용 환경변수나 Git에 넣지 않습니다. 로그인 후 설정 화면에서 사용자별로 저장합니다.

### 3. 마이그레이션과 서버 실행

```bash
uv run python main.py migrate
uv run python -m src.api.mcp_server
```

기본 로컬 포트는 `8000`입니다.

```bash
curl http://localhost:8000/health
```

예상 응답:

```json
{"status":"ok"}
```

서버 import 시에도 미적용 DB migration을 자동 실행합니다.

### 4. 로그인하고 사용자 설정 등록

브라우저에서 다음 주소를 엽니다.

```text
https://knowledge.lynply.com/settings
```

실제 로그인 흐름은 다음과 같습니다.

```text
/settings
  → /login
  → 외부 OAuth Authorization Server (PKCE)
  → /callback
  → Secure + HttpOnly + SameSite=Lax 세션 쿠키
  → /dashboard
```

설정 화면에서 다음 값을 등록합니다.

- OpenAI API key
- storage type: `s3` 또는 `r2`
- S3-compatible endpoint URL
- bucket name
- access key ID
- secret access key

비밀값은 `SETTINGS_ENCRYPTION_KEY`로 암호화되어 저장되며 조회 API에 원문으로 반환되지 않습니다. 설정 저장 후 해당 사용자 storage cache만 무효화됩니다.

### 5. Knowledge API key 발급

`/dashboard`에서 MCP용 API key를 발급합니다. 평문 key는 생성 직후에만 표시되므로 안전한 비밀 저장소에 보관합니다.

MCP 요청에는 다음 header가 필요합니다.

```http
Authorization: Bearer YOUR_KNOWLEDGE_API_KEY
```

### 6. MCP 클라이언트 연결

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

연결 뒤 `search_wiki_knowledge`를 호출해 인증·DB·embedding 설정을 함께 확인합니다.

## 핵심 워크플로

아래 JSON은 MCP 도구의 arguments 예시입니다. 실제 클라이언트 UI에서는 도구 이름을 선택하고 arguments만 입력할 수 있습니다.

### 일반 지식 검색

도구: `search_wiki_knowledge`

```json
{
  "query": "OAuth PKCE와 refresh token rotation 운영 규칙",
  "limit": 5
}
```

응답에는 검색 결과와 `Search Event ID`가 포함됩니다. 이후 검색 평가를 제출하려면 이 ID를 보관합니다.

일반 검색은 다음 자료만 대상으로 합니다.

- `public` 문서
- 현재 사용자가 소유한 `private` 문서
- 현재 live `qa/`, `topics/` corpus

`baselines/`와 `baseline-drafts/`는 vector, keyword, graph 검색에서 모두 제외됩니다.

### 새 지식 기록

도구: `commit_new_knowledge`

```json
{
  "title": "OAuth callback 오류 로깅 규칙",
  "description": "민감한 code와 state를 로그에 남기지 않는 운영 규칙",
  "tags": ["oauth", "security", "logging"],
  "content": "# 규칙\n\nCallback 실패 로그에는 오류 타입만 기록하고 authorization code, state, token, 내부 상세 메시지는 기록하지 않는다.",
  "visibility": "private"
}
```

결과:

1. `qa/YYYY-MM-DD/.../*.md`에 새 Q&A journal을 저장합니다.
2. 선택적으로 `topic_name`, `topic_update_text`가 있으면 topic 문서를 누적 갱신합니다.
3. 실제로 작성한 파일만 durable indexing queue에 등록합니다.
4. 인덱싱은 비동기로 처리됩니다.

topic도 함께 갱신하는 예시:

```json
{
  "title": "PKCE 운영 규칙 정리",
  "description": "PKCE 관련 새 운영 지식",
  "tags": ["oauth", "pkce"],
  "content": "# 새로 확인한 내용\n\nS256만 허용한다.",
  "topic_name": "Development/oauth",
  "topic_update_text": "## PKCE\n\nAuthorization Code flow에서는 S256 challenge만 허용한다.",
  "visibility": "private"
}
```

`commit_new_knowledge`는 저장과 queue 등록을 함께 수행하므로 곧바로 `run_database_indexing`을 중복 호출하지 않습니다.

### 외부에서 수정한 파일만 즉시 인덱싱

Obsidian, S3 console, 동기화 프로그램 등 MCP 밖에서 파일을 수정한 경우에만 사용합니다.

도구: `run_database_indexing`

```json
{
  "file_paths": [
    "qa/2026-07-19/1234-oauth-note/1234-oauth-note.md",
    "topics/Development/oauth.md"
  ]
}
```

- 전체 corpus가 아니라 지정한 Markdown 경로만 처리합니다.
- 경로를 생략한 전체 인덱싱 MCP 호출은 제공하지 않습니다.
- 실패한 durable job은 CLI/CronJob의 `retry-indexing`으로 재처리합니다.

## Inbox 사용법

Inbox는 읽고 검토하기 전 자료를 보관하는 영역입니다. Inbox 항목은 자동 인덱싱되거나 일반 Knowledge로 승격되지 않습니다.

### 웹에서 파일 또는 링크 보관

- `/inbox`에서 파일 업로드: 최대 25MB
- `/inbox`에서 HTTP/HTTPS 링크 등록
- 파일명은 안전하게 정규화됩니다.
- 링크에 user info가 포함된 URL은 거절합니다.

### MCP에서 이미 읽은 자료를 Markdown으로 보관

사용자가 저장을 요청한 뒤, 클라이언트 에이전트가 실제 원문을 읽고 Markdown으로 구조화했을 때 사용합니다.

도구: `create_inbox_markdown`

외부 링크 예시:

```json
{
  "title": "PostgreSQL advisory lock 문서 요약",
  "content": "# 요약\n\nTransaction-level advisory lock은 transaction 종료 시 자동 해제된다.",
  "source_kind": "external_link",
  "original_url": "https://www.postgresql.org/docs/current/explicit-locking.html",
  "media_type": "text/html",
  "extraction_complete": true,
  "warnings": [],
  "note": "기준본 동시성 설계 검토용"
}
```

첨부 파일 예시:

```json
{
  "title": "보안 검토 회의록",
  "content": "# 회의록\n\n검토된 결정과 미결 사항...",
  "source_kind": "chat_attachment",
  "original_filename": "security-review.pdf",
  "media_type": "application/pdf",
  "extraction_complete": false,
  "warnings": ["부록 표 2개는 OCR 품질이 낮음"],
  "note": "원문 대조 필요"
}
```

중요한 제한:

- `source_kind`: `chat_attachment`, `external_link`, `user_text`, `other`
- 외부 링크는 `original_url` 필수
- 원문을 읽지 못했다면 성공적으로 추출했다고 기록하지 않습니다.
- derived Markdown 최대 크기: 2MB
- Inbox MCP 읽기 최대 크기: 2MB
- binary 또는 지원하지 않는 파일은 metadata만 반환할 수 있습니다.

목록과 읽기:

```json
{"limit": 20}
```

도구: `list_inbox_items`

```json
{"item_id": "INBOX_ITEM_ID"}
```

도구: `read_inbox_item`

## 학습 워크플로

학습은 클라이언트 LLM이 사용자 답변의 의미를 평가하고, 서버는 세션·판정·복습 일정을 정규화하고 저장하는 구조입니다. 클릭이나 서버 추측을 정답으로 사용하지 않습니다.

### 1. 학습 팩 준비

도구: `prepare_learning_session`

```json
{
  "topic": "DDD Aggregate와 Application Service의 책임",
  "scope": "combined",
  "goal": "실제 주문 취소 유스케이스에 적용",
  "level": "practical",
  "duration_minutes": 20,
  "inbox_item_ids": ["OPTIONAL_INBOX_ITEM_ID"],
  "knowledge_limit": 5
}
```

- `scope`: `inbox`, `knowledge`, `combined`
- `level`: `beginner`, `practical`, `advanced`
- `duration_minutes`: 5~120
- `knowledge_limit`: 1~10
- 별도 범위 요청이 없으면 `combined`를 사용합니다.
- `due_review_summary.count > 0`이면 새 학습 전에 복습 항목이 있음을 알리고 우선 진행 여부를 묻습니다.
- 반환된 `first_question` 하나만 먼저 사용자에게 제시합니다.
- 사용자가 답하기 전에 근거와 정답 내용을 노출하지 않습니다.

### 2. 영속 세션 시작

준비 결과의 값을 그대로 사용합니다.

도구: `start_learning_session`

```json
{
  "topic": "DDD Aggregate와 Application Service의 책임",
  "requested_scope": "combined",
  "effective_scope": "knowledge",
  "goal": "실제 주문 취소 유스케이스에 적용",
  "level": "practical",
  "duration_minutes": 20,
  "first_question": "자료를 보지 않고 Aggregate가 지켜야 할 불변식을 설명해 주세요.",
  "sources": [
    {
      "source_type": "knowledge",
      "source_ref": "topics/Development/ddd.md",
      "relationship": "confirm",
      "metadata": {"title": "DDD 실전 정리"}
    }
  ],
  "client_request_id": "learning-start-20260719-001"
}
```

재시도될 수 있는 write에는 안정적인 `client_request_id`를 넣어 중복 저장을 방지합니다.

### 3. 답변 판정과 피드백 계획

클라이언트 LLM이 근거를 대조해 판정한 뒤 호출합니다.

도구: `plan_learning_feedback`

```json
{
  "assessment": "partial",
  "confidence": "high",
  "missing_concepts": ["트랜잭션 경계"],
  "misconceptions": [],
  "evidence_refs": ["topics/Development/ddd.md"],
  "hint": "상태 전이를 누가 호출하고 transaction을 누가 여는지 나눠 보세요.",
  "next_question": "주문 취소에서 Aggregate와 Application Service가 각각 맡는 일은 무엇인가요?",
  "learning_dimension": "comprehension",
  "transfer_level": "none",
  "support_level": "light"
}
```

- `assessment`: `mastered`, `partial`, `misconception`, `unknown`, `unverifiable`
- `confidence`: `low`, `medium`, `high`
- `partial`: `missing_concepts` 또는 `hint` 필요
- `misconception`: `misconceptions` 또는 `hint` 필요
- `unverifiable` 외 판정: 최소 하나의 `evidence_refs` 필요
- `learning_dimension`: `retrieval`, `comprehension`, `transfer`
- `transfer` 판정은 `transfer_level=near|far`가 필요합니다.
- `support_level`: `none`, `light`, `substantial`. 힌트가 있었다면 독립 숙달로 취급하지 않습니다.
- 응답의 `metacognitive_calibration`은 확신과 독립 성과를 `aligned`, `overconfident`, `underconfident`, `insufficient_evidence`로 구분합니다.

### 4. 답변 기록

도구: `record_learning_attempt`

```json
{
  "session_id": "SESSION_ID",
  "question_id": "QUESTION_ID",
  "answer": "사용자의 실제 답변",
  "assessment": "partial",
  "confidence": "high",
  "feedback_plan": {
    "next_action": "hint_then_retry",
    "should_reask": true,
    "suggested_review_days": 3,
    "review_priority": "medium",
    "learning_evidence": {
      "dimension": "comprehension",
      "transfer_level": "none",
      "support_level": "light",
      "independent_success": false
    }
  },
  "missing_concepts": ["트랜잭션 경계"],
  "misconceptions": [],
  "evidence_refs": ["topics/Development/ddd.md"],
  "next_question": "같은 사례를 책임별로 다시 설명해 주세요.",
  "next_question_type": "application",
  "next_evidence_refs": ["topics/Development/ddd.md"],
  "client_request_id": "attempt-SESSION_ID-QUESTION_ID-1",
  "next_transfer_level": "near"
}
```

다른 대화에서 이어갈 때:

```json
{"session_id": "SESSION_ID"}
```

도구: `resume_learning_session`. `session_id`를 생략하면 가장 최근 활성 세션을 조회합니다.

완료 전 독립 인출·이해·near/far 전이 증거를 확인합니다.

```json
{"session_id": "SESSION_ID"}
```

도구: `prepare_learning_completion`

`ready_to_complete=false`이면 `next_question_contract`에 맞는 변형 문제를 출제하고 답변을 더 기록합니다. 새 세션은 네 증거가 모두 확인되기 전에는 `complete_learning_session`을 호출해도 `active`로 유지됩니다. 기존 schema version 1 세션은 호환성을 위해 이 완료 gate를 소급 적용하지 않습니다.

완료할 때:

```json
{
  "session_id": "SESSION_ID",
  "summary": "Aggregate 불변식과 Application Service의 orchestration 책임을 사례로 구분함"
}
```

도구: `complete_learning_session`

### 5. 복습

```json
{"limit": 10}
```

도구: `list_due_learning_reviews`

기한이 된 항목을 한 문제씩 처리합니다. 반환된 `question_contract`를 사용해 원 질문을 그대로 반복하지 않는 `near` 또는 `far` 지연 전이 문제를 만들고, 사용자가 답하기 전에는 이전 답·정답·근거를 노출하지 않습니다.

```json
{
  "review_id": "REVIEW_ID",
  "answer": "사용자의 복습 답변",
  "assessment": "mastered",
  "confidence": "medium",
  "feedback_plan": {
    "next_action": "advance",
    "suggested_review_days": 7,
    "review_priority": "low",
    "learning_evidence": {
      "dimension": "transfer",
      "transfer_level": "near",
      "support_level": "none",
      "independent_success": true
    }
  },
  "client_request_id": "review-REVIEW_ID-20260719"
}
```

도구: `record_learning_review`

near 전이를 무힌트로 성공하면 다음 복습은 far로 승격됩니다. 힌트를 사용했거나 실패하면 현재 전이 수준을 유지합니다.

### 6. 학습 결과를 Knowledge로 만들기

자동 승격하지 않습니다.

1. `prepare_learning_knowledge_candidates`로 세션 근거 팩을 조회합니다.
2. 클라이언트가 후보 초안을 만듭니다.
3. `stage_learning_knowledge_candidates`로 pending 후보를 저장합니다.
4. 사용자에게 후보를 하나씩 보여줍니다.
5. 사용자의 명시적 선택에만 `review_learning_knowledge_candidate`를 호출합니다.
6. `approved=true` 후보만 `commit_learning_knowledge_candidate`로 Knowledge에 저장합니다.

```json
{"session_id": "SESSION_ID"}
```

```json
{
  "session_id": "SESSION_ID",
  "candidates": [
    {
      "candidate_type": "learning_record",
      "title": "Application Service의 책임",
      "description": "도메인 객체 호출과 transaction orchestration의 경계",
      "tags": ["ddd", "application-service"],
      "content": "# Application Service\n\nApplication Service는 유스케이스 흐름과 transaction을 조정하고 Aggregate의 상태 전이 규칙을 대신 소유하지 않는다.",
      "evidence_refs": ["topics/Development/ddd.md"],
      "client_request_id": "candidate-SESSION_ID-application-service"
    }
  ]
}
```

후보 schema는 서버 validation 결과를 따라야 하며 conflict/correction 후보를 기존 문서에 자동 덮어쓰지 않습니다.

```json
{
  "candidate_id": "CANDIDATE_ID",
  "approved": true,
  "note": "예시와 근거 확인 완료"
}
```

```json
{"candidate_id": "CANDIDATE_ID"}
```

마지막 호출이 성공하면 일반 Knowledge 저장과 durable indexing queue 등록이 함께 수행됩니다.

## 업무 기준본 사용법

기준본은 “현재 지식 전체”가 아니라 특정 업무 목적에 대해 사용자가 검토·확정한 문서 snapshot입니다.

### 저장 및 검색 격리

```text
초안 manifest
baseline-drafts/<draft_id>/baseline.yaml

확정 manifest
baselines/<name>/<version>/baseline.yaml

확정 문서 snapshot
baselines/<name>/<version>/documents/<source_path>
```

전용 DB 테이블:

- `knowledge_baseline_drafts`
- `knowledge_baseline_releases`
- `knowledge_baseline_documents`

일반 `knowledge_documents` 검색은 기준본 디렉터리를 제외합니다. 기준본 검색은 인증된 `owner_id`와 명시한 `release_id`를 함께 강제합니다.

### 1. 신규 기준본 초안 준비

사용자가 “이 문서들로 OAuth 운영 기준본 v1 초안을 만들어줘”처럼 명시적으로 요청했을 때만 호출합니다.

도구: `prepare_knowledge_baseline`

```json
{
  "name": "oauth-operations",
  "version": "v1",
  "purpose": "인증 서버 운영 변경 검토와 장애 대응",
  "source_paths": [
    "topics/Development/oauth.md",
    "qa/2026-07-19/1234-token-rotation/1234-token-rotation.md"
  ],
  "base_release_id": null
}
```

허용되는 source는 `qa/` 또는 `topics/` 아래 Markdown뿐입니다. `baselines/`, `baseline-drafts/`, `inbox/`, 상위 경로 이동은 허용하지 않습니다.

이 호출은:

- 원문 SHA-256을 기록합니다.
- `baseline-drafts/`에 pending manifest를 만듭니다.
- `draft_id`를 반환합니다.
- 확정본을 만들지 않습니다.

### 2. 초안 검토 후 명시적 확정

`draft_id`, 문서 목록, 목적, version을 사용자에게 보여주고 사용자가 확정을 명시했을 때만 호출합니다.

도구: `confirm_knowledge_baseline`

```json
{"draft_id": "DRAFT_ID_FROM_PREPARE"}
```

확정은 fail-closed입니다.

- 초안 생성 후 원문 hash가 달라졌으면 거절합니다.
- 최신 원문과 일치하는 live index가 없으면 먼저 해당 문서를 인덱싱하라고 거절합니다.
- 동일 owner/name/version 확정은 DB advisory lock으로 직렬화합니다.
- snapshot 파일과 검색 DB row가 같은 content hash를 갖도록 강제합니다.
- DB 확정 실패 시 이 작업이 만든 확정 디렉터리를 보상 정리합니다.
- 성공하면 `release_id`를 반환합니다.

### 3. 특정 확정본을 업무에 사용

사용자가 “release X 기준으로 검토해줘”처럼 적용을 명시했을 때만 호출합니다.

도구: `search_knowledge_baseline`

```json
{
  "query": "refresh token 탈취 의심 시 폐기와 재발급 순서",
  "release_id": "RELEASE_ID_FROM_CONFIRM",
  "limit": 5
}
```

- 지정한 release만 검색합니다.
- 결과의 `file_path`는 확정 snapshot 경로입니다.
- 결과가 없으면 no-answer를 반환합니다.
- 일반 `search_wiki_knowledge`로 자동 전환하지 않습니다.
- 다른 사용자의 release_id는 사용할 수 없습니다.

### 4. 기준본 최신화: 기존 버전을 수정하지 않고 v2 생성

사용자가 “v1을 기준으로 v2 초안을 준비해줘”라고 명시했을 때:

```json
{
  "name": "oauth-operations",
  "version": "v2",
  "purpose": "2026 Q3 token rotation 정책 반영",
  "source_paths": [
    "topics/Development/oauth.md",
    "qa/2026-07-19/1500-q3-policy/1500-q3-policy.md"
  ],
  "base_release_id": "V1_RELEASE_ID"
}
```

그 다음 v2 `draft_id`를 다시 명시적으로 확정합니다.

중요:

- v1 파일과 DB row는 수정하지 않습니다.
- v2가 확정되어도 업무가 자동으로 v2를 사용하지 않습니다.
- 이후 업무 요청에서 v2 `release_id`를 명시해야 v2가 적용됩니다.

## 검색 피드백

일반 검색 응답의 `Search Event ID`에 대해 사용자가 직접 평가했을 때만 제출합니다.

도구: `submit_search_feedback`

```json
{
  "search_id": "SEARCH_EVENT_ID",
  "relevant_paths": ["topics/Development/oauth.md"],
  "partially_relevant_paths": [],
  "irrelevant_paths": ["qa/old-token-note.md"],
  "satisfaction": "partial",
  "failure_reasons": ["wrong_order"],
  "expected_no_answer": false,
  "missing_answer_path": "qa/2026-07-19/new-policy.md",
  "notes": "최신 정책 문서가 상위에 나와야 함",
  "result_feedback": [
    {
      "file_path": "topics/Development/oauth.md",
      "relevance_grade": 3,
      "issue_reasons": [],
      "relation_helpful": true,
      "ontology_context_grade": 2
    }
  ],
  "expected_relations": [],
  "expected_graph_paths": [],
  "forbidden_paths": [],
  "expected_rule_types": [],
  "ontology_notes": null
}
```

- `satisfaction`: `satisfied`, `partial`, `dissatisfied`
- result별 `relevance_grade`: 0~3
- no-answer가 정답이면 `expected_no_answer=true`
- UI open/copy/cite/follow/reformulate/abandon 행동은 별도 telemetry이며 relevance 정답으로 간주하지 않습니다.

## 전체 MCP 도구 레퍼런스

공개 MCP 계약은 23개 도구 이름과 입력 필드로 테스트에 고정되어 있습니다.

| 도구 | 필수 입력 | 선택 입력 / 기본값 |
| --- | --- | --- |
| `search_wiki_knowledge` | `query` | `limit=5` |
| `prepare_knowledge_baseline` | `name`, `version`, `purpose`, `source_paths` | `base_release_id=null` |
| `confirm_knowledge_baseline` | `draft_id` | 없음 |
| `search_knowledge_baseline` | `query`, `release_id` | `limit=5` |
| `create_inbox_markdown` | `title`, `content` | `source_kind=user_text`, `original_filename`, `original_url`, `media_type`, `extraction_complete=true`, `warnings`, `note` |
| `list_inbox_items` | 없음 | `limit=50` |
| `read_inbox_item` | `item_id` | 없음 |
| `prepare_learning_session` | `topic` | `scope=combined`, `goal=understand`, `level=practical`, `duration_minutes=20`, `inbox_item_ids`, `knowledge_limit=5` |
| `plan_learning_feedback` | `assessment` | `confidence=medium`, `missing_concepts`, `misconceptions`, `evidence_refs`, `hint`, `next_question`, `learning_dimension=retrieval`, `transfer_level=none`, `support_level=none` |
| `start_learning_session` | `topic`, `requested_scope`, `effective_scope`, `goal`, `level`, `duration_minutes`, `first_question` | `sources`, `client_request_id` |
| `record_learning_attempt` | `session_id`, `question_id`, `answer`, `assessment`, `confidence`, `feedback_plan` | `missing_concepts`, `misconceptions`, `evidence_refs`, `next_question`, `next_question_type=retrieval`, `next_evidence_refs`, `client_request_id`, `next_transfer_level=none` |
| `resume_learning_session` | 없음 | `session_id=null` |
| `prepare_learning_completion` | `session_id` | 없음 |
| `complete_learning_session` | `session_id` | `summary` |
| `list_due_learning_reviews` | 없음 | `limit=20` |
| `record_learning_review` | `review_id`, `answer`, `assessment`, `confidence`, `feedback_plan` | `client_request_id` |
| `prepare_learning_knowledge_candidates` | `session_id` | 없음 |
| `stage_learning_knowledge_candidates` | `session_id`, `candidates` | 없음 |
| `review_learning_knowledge_candidate` | `candidate_id`, `approved` | `note` |
| `commit_learning_knowledge_candidate` | `candidate_id` | 없음 |
| `submit_search_feedback` | `search_id` | relevance/no-answer/result/ontology 평가 필드 |
| `commit_new_knowledge` | `title`, `description`, `tags`, `content` | `topic_name`, `topic_update_text`, `visibility=private` |
| `run_database_indexing` | `file_paths` | 없음 |

정확한 필드 순서는 `tests/test_mcp_tool_contract.py`, validation과 설명은 `src/api/mcp_server.py`를 기준으로 합니다.

## 웹 화면과 API

### 화면

| Path | 용도 |
| --- | --- |
| `/dashboard` | API key 관리 및 요약 |
| `/settings` | 읽기 전용 사용자 설정 확인 |
| `/settings/edit` | OpenAI/S3/R2 설정 수정 |
| `/documents` | owner-scoped Knowledge 문서 탐색 |
| `/inbox` | 파일·링크 보관 |
| `/learning` | 학습 현황 dashboard |
| `/search-feedback` | 최근 검색과 명시적 평가 |
| `/search-feedback/{search_id}` | 검색 graph 시각화와 결과 검토 |

모든 화면은 세션 보호를 받으며 세션이 없거나 만료되면 `/login`으로 이동합니다. 만료된 cookie는 삭제됩니다.

### API

| Method | Path | 용도 |
| --- | --- | --- |
| GET | `/health` | 상태 확인 |
| GET | `/login` | OAuth PKCE 시작 |
| GET | `/callback` | code 교환 및 서버 세션 생성 |
| POST | `/logout` | 서버 세션 revoke 및 cookie 삭제 |
| GET, PUT | `/api/settings` | 사용자 설정 조회·저장 |
| GET, POST | `/api/keys` | API key 목록·생성 |
| DELETE | `/api/keys/{key_id}` | 현재 auth subject의 key 폐기 |
| GET | `/api/documents` | owner 문서 목록 |
| GET | `/api/documents/{file_path}` | owner 문서 읽기 |
| GET | `/api/inbox` | owner Inbox 목록 |
| POST | `/api/inbox/links` | HTTP/HTTPS 링크 보관 |
| POST | `/api/inbox/files` | 파일 업로드 |
| GET | `/api/inbox/{item_id}/file` | Inbox 파일 다운로드 |
| GET | `/api/learning/dashboard` | owner 학습 지표 |
| GET | `/api/search-feedback/events` | 최근 owner 검색 이벤트 |
| GET | `/api/search-feedback/{search_id}/graph` | 검색 graph 조회 |
| PUT | `/api/search-feedback/{search_id}` | 명시적 검색 평가 저장 |
| POST | `/api/search-feedback/{search_id}/behavior` | open/copy/cite 등 행동 기록 |
| POST | `/mcp` | Streamable HTTP MCP |

host 경계:

- `knowledge.lynply.com`에서 `/mcp`는 404입니다.
- `mcp.lynply.com`에서 Settings 페이지와 웹 API는 404입니다.

## Page Bundle과 문서 형식

Markdown과 첨부 자산은 글 단위 폴더로 묶습니다.

```text
qa/
└── 2026-07-19/
    └── 1430-kubernetes-setup/
        ├── 1430-kubernetes-setup.md
        └── assets/
            ├── architecture.png
            └── architecture.png.md

topics/
└── Development/
    ├── llm-wiki.md
    └── archive/
        └── llm-wiki_20260719_1430.md
```

예시 frontmatter:

```yaml
---
title: PostgreSQL 연결 설정
description: 운영 연결 구성 기록
tags: [postgresql, operations]
visibility: private
---
```

- `private`: 소유자만 검색. `commit_new_knowledge` 기본값.
- `public`: 모든 인증 사용자가 검색 가능.
- `qa/`, `topics/` 경로만으로 visibility가 결정되지는 않습니다.

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

- vector similarity, lexical rank, RRF score, citation count를 별도 신호로 표시합니다.
- citation count는 동률 보조 신호이며 관련성 임계치를 통과시키지 않습니다.
- graph 관련 문서는 직접 결과와 섞지 않고 `graph_context`로 분리합니다.
- graph/ontology 보조 기능 실패는 direct indexing/search를 실패시키지 않습니다.
- 리랭커는 현재 운영 설정에서 비활성화되어 있습니다.

주요 설정 예시:

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
DOCUMENT_EXPANSION_ENABLED=false
```

`DOCUMENT_EXPANSION_ENABLED=true`이면 예상 질문과 검색 키워드를 embedding 입력에만 추가하고 원문 `content`는 변경하지 않습니다.

## CLI 운영

CLI는 DB에 저장된 해당 owner의 OpenAI/S3/R2 설정을 사용합니다.

```bash
# 전체 owner corpus 인덱싱
uv run python main.py index --owner-id USER_ID

# 일반 live 검색
uv run python main.py search "쿠버네티스 배포 절차" --limit 5 --owner-id USER_ID

# 미적용 migration 실행
uv run python main.py migrate

# due/failed durable indexing job 재시도
uv run python main.py retry-indexing --limit 100

# retry schedule과 최대 시도 횟수를 무시한 강제 재시도
uv run python main.py retry-indexing --limit 100 --force
```

검색 평가:

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

uv run python main.py diagnose-search-stages \
  --owner-id USER_ID \
  --queries tests/search_quality_blind_queries.json \
  --answers tests/search_quality_blind_answers.json

uv run python main.py check-direct-regression \
  --candidate path/to/candidate-report.json
```

Blind query, answer, prediction 파일은 Git ignore 대상입니다.

## 데이터베이스 마이그레이션

Alembic이나 SQLAlchemy migration 대신 `src/core/database/migrations.py`의 버전 migration을 사용합니다. PostgreSQL advisory lock으로 여러 인스턴스의 동시 실행을 직렬화하고 `knowledge_schema_migrations`에 이력을 저장합니다.

현재 migration은 1~19입니다.

| 버전 | 내용 |
| --- | --- |
| 1 | 문서, edge, citation, topic, audit, user, API key 코어 스키마 |
| 2 | `owner_id`·`visibility` 멀티테넌시 제약조건 |
| 3 | durable indexing job queue |
| 4 | 암호화된 사용자 OpenAI·S3/R2 설정 |
| 5 | pgvector HNSW와 PostgreSQL FTS index |
| 6 | 원격 사용자 저장소 강제 전환 |
| 7 | 기본 저장소 S3 확정 |
| 8 | 검색 이벤트와 기본 human feedback |
| 9 | 검색 relevance/satisfaction label 확장 |
| 10 | 검색 행동 telemetry |
| 11 | 학습 session과 attempt |
| 12 | spaced learning review |
| 13 | 승인 기반 learning knowledge candidate |
| 14 | ontology schema와 relation evidence |
| 15 | ontology shadow event |
| 16 | ontology relation lifecycle 확장 |
| 17 | ontology 검색 피드백 확장 |
| 18 | immutable knowledge baseline draft/release/document |
| 19 | 학습 차원·near/far 전이·힌트 지원 수준·독립 성공·메타인지 보정 증거 |

## Kubernetes 배포

배포 파일은 `k8s/`에 있습니다.

- `k8s/configmap.yaml`: 비밀이 아닌 검색·Redis·서비스 설정
- `k8s/mcp-deployment.yaml`: MCP와 Settings Web 애플리케이션
- `k8s/mcp-secrets.yaml.example`: 실제 값이 없는 Secret key 구조
- indexing worker/CronJob은 애플리케이션과 동일 image tag를 사용해야 합니다.

운영 반영 순서:

1. 테스트와 migration DDL 검증
2. image build/import
3. application과 indexing CronJob의 image tag 동기화
4. rollout 확인
5. `knowledge.lynply.com`과 `mcp.lynply.com/mcp` 외부 계약 확인

실제 Secret manifest, `.env`, 감사 로그, blind answer는 커밋하지 않습니다.

## 주요 코드 위치

- `src/api/mcp_server.py`: 23개 MCP 공개 계약과 app composition
- `src/api/agent_tool.py`: MCP facade
- `src/api/handlers/`: MCP 내부 use-case handler
- `src/baselines/`: 기준본 준비·확정·검색·동시성/보상
- `src/core/database/migrations.py`: migration 1~19
- `src/core/storage/factory.py`: owner별 S3/R2 생성과 cache
- `src/indexing/`: durable 증분 indexing
- `src/learning/domain/`: 학습 상태 전이 규칙
- `src/learning/application/`: 학습 준비·세션 use case
- `src/retrieval/domain/`: retrieval policy와 projection
- `src/retrieval/application/`: 검색 orchestration
- `src/retrieval/infrastructure/`: PostgreSQL retrieval, reranker, observer
- `src/settings/web.py`: Settings Web composition root와 인증 helper
- `src/settings/web_*.py`: auth/page/API/host dispatcher Router
- `src/wiki/domain/`: 지식 commit command와 경로 정책
- `src/wiki/application/`: journal/topic 저장과 queue orchestration
- `src/wiki/composition.py`: owner storage와 durable queue 조립

## 보안과 장애 처리

- 인증된 `owner_id`는 UI filter가 아니라 DB query와 storage 선택에 사용됩니다.
- API key 목록·생성·폐기는 인증 서버 `auth_id` 범위로 제한됩니다.
- OAuth callback은 code/state/token/내부 상세를 로그에 남기지 않습니다.
- session cookie는 Secure, HttpOnly, SameSite=Lax입니다.
- logout은 서버 session을 revoke하고 cookie를 삭제합니다.
- S3/R2 설정이 없으면 로컬 disk로 fallback하지 않습니다.
- Inbox URL은 HTTP/HTTPS만 허용하고 user info가 포함된 URL을 거절합니다.
- document와 Inbox file path는 owner scope와 안전한 prefix를 검증합니다.
- 필수 embedding/storage/DB 실패는 성공이나 clean 결과로 위장하지 않습니다.
- 기준본 no-answer는 live 검색으로 fallback하지 않습니다.

## 에이전트 사용 원칙

`.agents/AGENTS.md`의 기본 흐름:

1. 개인 지식이 필요한 요청은 먼저 `search_wiki_knowledge`로 검색합니다.
2. 새 지식이나 업무 규칙을 기록할 때 `commit_new_knowledge`를 사용합니다.
3. `commit_new_knowledge`가 저장과 queue 등록을 수행하므로 중복 인덱싱하지 않습니다.
4. 외부 수정 파일을 즉시 반영할 때만 `run_database_indexing(file_paths=[...])`을 사용합니다.
5. 파일·링크 Inbox 저장은 원문을 실제로 읽고 사용자 확인을 받은 뒤 수행합니다.
6. 학습 시작 시 `due_review_summary`를 확인하고, 복습이 있으면 새 학습 전에 우선 진행 여부를 묻습니다.
7. 학습은 답변 전 근거를 노출하지 않고, 판정은 evidence 기반 client LLM 평가로 기록합니다.
8. 인출·이해·near/far 전이와 실제 힌트 수준을 구분하며, 힌트가 있으면 독립 숙달로 기록하지 않습니다.
9. 세션 종료 전 `prepare_learning_completion`을 확인하고 부족한 증거의 변형 문제를 이어갑니다.
10. 복습에서는 원 질문을 반복하지 않고 `question_contract`에 따른 지연 전이 문제를 출제합니다.
11. 학습 결과의 Knowledge 승격은 후보별 사용자 승인 후에만 수행합니다.
12. 기준본 준비·확정·업무 적용·후속 버전 선택은 모두 사용자의 명시적 요청으로만 수행합니다.

## 검증

주요 호환성 gate:

```bash
uv run pytest tests/test_mcp_tool_contract.py -q
uv run pytest tests/test_knowledge_baselines.py tests/test_live_search_excludes_baselines.py -q
uv run pytest tests/test_settings_web.py tests/test_api_keys_web.py -q
uv run pytest -q
```

문서와 코드가 어긋나는지 확인할 때는 다음 파일을 함께 대조합니다.

- MCP 도구: `src/api/mcp_server.py`, `tests/test_mcp_tool_contract.py`
- 웹 경로: `src/settings/web_*.py`, `tests/test_settings_web.py`
- migration: `src/core/database/migrations.py`, `tests/test_database_migrations.py`
- 기준본: `src/baselines/`, `tests/test_knowledge_baselines.py`
