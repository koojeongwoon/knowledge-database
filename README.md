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
  qa/
  └── 2026-06-21/
      └── 1430-kubernetes-setup/                  [글 단위 폴더]
          ├── 1430-kubernetes-setup.md            (본문 마크다운 파일)
          └── assets/                             [자동 생성된 미디어 하위 폴더]
              ├── architecture-diagram.png        (원본 이미지)
              └── architecture-diagram.png.md     (VLM이 생성한 이미지 사이드카 캐시)
  ```
* **Obsidian 권장 설정**:
  * **새 첨부 파일을 생성할 위치**: `현재 폴더 아래의 하위 폴더` (하위 폴더 이름: `assets`)
  * **새로 생성하는 링크 형식**: `상대 경로`
  * **항상 업데이트되는 내부 링크**: `활성화`

---

## 2. 시스템 핵심 기능 및 워크플로우

### 1) 전체 인덱싱 및 이미지 전처리 워크플로우 (`main.py index`)

인덱싱 스크립트 실행 시 텍스트 파싱 전에 이미지 전처리가 먼저 가동되며, **2중 증분(Incremental) 필터링**을 거쳐 최소한의 API 비용과 시간만을 소모합니다.

```mermaid
flowchart TD
    subgraph A ["이미지 전처리 및 캐싱"]
        Start["인덱싱 명령"] --> ScanImg["target_dirs 내 이미지 스캔"]
        ScanImg --> HashComp{"MD5 해시 비교"}
        HashComp -->|변경 없음| SkipVLM["VLM 분석 스킵"]
        HashComp -->|신규 또는 수정| CallVLM["OpenAI Vision 분석"]
        CallVLM --> CreateSidecar["사이드카 캐시 파일 생성"]
        SkipVLM --> CleanupOrphan["고아 캐시 파일 자동 삭제"]
    end

    subgraph B ["텍스트 임베딩 및 DB 적재"]
        CreateSidecar --> ScanMD
        CleanupOrphan --> ScanMD
        ScanMD["사이드카 포함 마크다운 스캔"] --> FileHashComp{"파일 해시 비교"}
        FileHashComp -->|일치| SkipDB["임베딩 및 DB 적재 스킵"]
        FileHashComp -->|변경| ParseMD["마크다운 파싱 및 관계 추출"]
        ParseMD --> Chunking["이중 청킹 분할"]
        Chunking --> Embed["배치 임베딩 생성"]
        Embed --> Upsert["PostgreSQL pgvector 적재"]
    end
```

* **VLM 이미지 캡셔닝 (`gpt-4o-mini`)**: 이미지 속 오브젝트 나열(객체 감지 대체), 전체 시각 스타일(모던, 코지, 와이어프레임 등) 묘사, OCR 글자 추출을 수행하여 `type: ImageSummary` 프론트매터를 가진 캐시 문서를 자동 생성합니다.
* **고아 사이드카 청소**: 로컬에서 원본 이미지 파일만 삭제되었을 경우, 매핑된 캐시 파일(`.png.md`)을 감지하여 로컬에서 자동 삭제하고 DB에서도 관련 벡터 데이터를 동기화하여 제거합니다.

---

## 3. 검색 및 RAG 컨텍스트 융합 워크플로우 (`src/searcher.py`)

사용자 쿼리가 들어왔을 때, 단순 벡터 검색에 의존하지 않고 다단계 하이브리드 리트리버를 거쳐 최종 생성 모델에 컨텍스트를 반환합니다.

```mermaid
sequenceDiagram
    autonumber
    actor Agent as "AI Agent"
    participant Searcher as "WikiSearcher"
    participant DB as "DatabaseManager"
    
    Agent->>Searcher: search query
    Searcher->>DB: 1. Vector Search
    Searcher->>DB: 2. Keyword Search
    DB-->>Searcher: 후보군 문서 반환
    Note over Searcher: 3. RRF 결합 및 랭킹 정렬
    Note over Searcher: 4. Cross-Encoder 리랭킹
    Note over Searcher: 5. Parent-Child 부모 문맥 교체
    Searcher->>DB: 6. Graph-Link 연관 문서 확장 요청
    DB-->>Searcher: 연결 문서 반환
    Note over Searcher: 7. 멀티모달 이미지 경로 매핑
    Searcher-->>Agent: 최종 컨텍스트 데이터 전달
```

1. **RRF 하이브리드**: 코사인 유사도 벡터 검색과 PostgreSQL GIN 인덱스 기반 Full-Text Search를 RRF 방식으로 융합합니다.
2. **Parent-Child RAG**: 데이터가 쪼개져 소실되는 것을 방지하기 위해, 문서는 300자 내외로 조밀하게 임베딩하고 검색 결과로 반환할 때는 더 넓은 부모 문맥(`parent_content`)으로 변경하여 반환합니다.
3. **Graph-Link RAG**: 매칭된 문서 본문 내에 적힌 `[[WikiLink]]` 엣지를 데이터베이스(`knowledge_edges`)에서 역추적하여 1촌 연결된 연관 문서를 검색 결과에 함께 확장 포함시킵니다.
4. **이미지 컨텍스트 결합**: 반환된 텍스트 중 `type: ImageSummary`가 존재할 시 이미지 파일의 상대경로를 에이전트에 함께 넘겨주어, 최종 에이전트 생성(Generation) 단계에서 원본 이미지 파일 바이트를 Vision 모델에 직접 전달할 수 있게 합니다.

---

## 4. 코드 설계 및 주요 모듈 구성

### 1) 백엔드 핵심 코드 파일 정보
* [pyproject.toml](file:///Users/jw/__dev/knowledge/pyproject.toml): `hatchling` 및 `uv` 표준을 따르는 의존성 설정 파일.
* [src/image_processor.py](file:///Users/jw/__dev/knowledge/src/image_processor.py): 신규 이미지 스캔, MD5 증분 검사, VLM API 요청, 고아 캐시 파일 자동 삭제 담당.
* [src/indexer.py](file:///Users/jw/__dev/knowledge/src/indexer.py): 로컬 `.md` 스캔 및 DB 저장 비교(증분), 위키링크 그래프 관계 적재, 문맥 보존용 이중 청킹 및 배치 임베딩.
* [src/database.py](file:///Users/jw/__dev/knowledge/src/database.py): pgvector 연결 설정, 테이블/인덱스(HNSW, FTS GIN) 생성 및 SQL 유사도 쿼리 실행.
* [src/searcher.py](file:///Users/jw/__dev/knowledge/src/searcher.py): RRF 순위 합산 알고리즘 및 Cross-Encoder 리랭킹, Graph Extension 매핑 및 Parent-Child 복원 필터.
* [src/agent_tool.py](file:///Users/jw/__dev/knowledge/src/agent_tool.py): 에이전트 전용 호출 툴 정의 ([retrieve_wiki_knowledge](file:///Users/jw/__dev/knowledge/src/agent_tool.py#L8), [commit_wiki_knowledge](file:///Users/jw/__dev/knowledge/src/agent_tool.py#L59)).

---

## 5. 설치 및 시작 가이드

### 1) 환경 변수 설정 (`.env`)
`.env` 파일을 루트 디렉토리에 작성합니다.
```env
DB_HOST=localhost
DB_PORT=54320
DB_NAME=knowledge_db
DB_USER=postgres
DB_PASSWORD=postgres

# 임베딩 공급자: fake, openai, bge-m3
EMBEDDING_PROVIDER=openai
OPENAI_API_KEY=sk-proj-...
EMBEDDING_DIM=1536

# 지식베이스(Obsidian Vault) 루트 경로 (기본값: '.' 현재 프로젝트 디렉토리)
WIKI_DIR=.
```

* **`WIKI_DIR` 환경 변수**: 프로젝트 외부의 다른 폴더 경로(예: iCloud/Dropbox에 동기화되는 실제 Obsidian Vault 디렉토리)를 지식베이스 루트로 연동하고 싶을 때 사용합니다. 절댓값 혹은 상대경로를 지정할 수 있으며 지정된 경로 내의 `qa/`, `topics/`, `assets/`, `attachments/`를 자동으로 스캔 및 동기화합니다.


### 2) 가상환경 설정 및 의존성 설치 (`uv` 권장)
```bash
# 가상환경 생성 및 패키지 설치
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
  python main.py search "쿠버네티스 레이아웃이 깨질 때 해결법" --limit 2
  ```
