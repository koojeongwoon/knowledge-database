---
name: commit_wiki_knowledge
description: 대화를 통해 도출된 새로운 지식을 일관적인 포맷의 파일(qa/ 저널)로 로컬 디바이스에 저장하고, 기존 주제별 노트(topics/)에 누적 합성(Synthesis)합니다.
allowed-tools:
  - write_file
metadata:
  version: 0.3.0
  author: Antigravity
---

# commit_wiki_knowledge

대화 중에 중요한 의사결정 사항, 합의된 설계 가이드라인, 혹은 저장할 가치가 있는 명확한 Q&A 지식이 확정되었을 때 호출하십시오. 본 문서에 명시된 규칙을 준수하여 마크다운 노트를 파일로 영속화합니다. (주의: 데이터베이스 인덱싱은 이 스킬에서 실행하지 않습니다.)

## 1. Parameters (매개변수)

* `title` (string, Required): Q&A 저널 파일의 요약 한글 제목 (예: '쿠버네티스 인프라 연동 의사결정')
* `description` (string, Required): Q&A 저널에 대한 1줄 요약 설명
* `tags` (array of strings, Required): 관련 태그 배열 (예: `["k8s", "postgresql"]`)
* `content` (string, Required): Q&A 저널 본문 내용 (마크다운 포맷 권장, 질답 텍스트 전체)
* `topic_name` (string, Optional): 누적 업데이트하거나 신규 생성할 주제 이름 (예: 'postgresql')
* `topic_update_text` (string, Optional): 주제 노트에 새로 덧붙여(Append) 합성할 본문 텍스트
* `image_paths` (array of strings, Optional): 복사 및 링크할 첨부 이미지 경로 배열 (예: `["/path/to/img.png"]`)
* `resource_paths` (array of strings, Optional): 복사 및 링크할 첨부 리소스 파일/오디오 경로 배열 (예: `["/path/to/voice.mp3", "/path/to/doc.pdf"]`)
* `resource_summaries` (array of objects, Optional): 첨부 파일에 대한 정교한 요약 메타데이터 및 본문 배열. 각 객체는 다음 필드를 포함해야 합니다:
  * `file_path` (string): 분석할 원본 파일의 로컬 경로
  * `type` (string): 미디어 타입 (`ImageSummary` \| `AudioSummary` \| `DocumentSummary`)
  * `title` (string): RAG 검색에 활용될 자원 요약용 명확한 제목
  * `description` (string): 자원에 대한 1줄 설명
  * `tags` (array of strings): 자원과 관련된 태그 목록
  * `content` (string): 텍스트 변환된 풍부한 마크다운 본문 (예: 회의 타임라인, 파싱된 표, OCR 텍스트 등)

## 2. Entrypoint (진입점)

* **Type**: Python Function Call
* **Entrypoint**: `src.api.agent_tool:commit_wiki_knowledge`
* **Runtime**: `.venv/bin/python`

---

## 3. 지식 영속화 및 누적 합성 세부 지침 (Core Constraints)

에이전트는 이 스킬을 실행할 때 아래 마크다운 작성 및 연결 법식을 강제로 준수해야 합니다.

### 1) 파일명 명명 규칙 (Naming Convention)
* **Q&A 저널 (`qa/`)**: `qa/YYYY-MM-DD/HHMM-[주제어-케밥케이스].md` 형식으로 자동 저장됩니다.
* **주제별 정리본 (`topics/`)**: `topics/[주제어-케밥케이스].md` 형식으로 자동 저장됩니다.

### 2) Frontmatter 메타데이터 규격 (OKF 호환)
모든 마크다운 상단에는 반드시 YAML Frontmatter가 들어가야 하며, 아래 양식을 유지합니다.
* **QAJournal (Q&A용)**: `type: QAJournal`, `title`, `description`, `tags`, `timestamp` (UTC ISO 포맷), `source: agent-commit` 필수.
* **TopicSummary (토픽용)**: `type: TopicSummary`, `title`, `description`, `tags`, `timestamp` (최종 갱신 시간) 필수.
* **수동 연결 강도 제어 (`custom_relations`)**: 특정 위키링크([[WikiLink]]) 간의 연관 강도(가중치)를 수동 지정하고 싶을 때 Frontmatter에 아래와 같이 `custom_relations` 리스트를 추가할 수 있습니다.
  ```yaml
  custom_relations:
    - link: "[[연결할토픽]]"
      weight: 1.5  # 실수형 가중치 (기본값: 1.0, 권장 범위: 0.1 ~ 2.0)
  ```
  * 이 지정값은 RAG 그래프 확장 시 $\text{similarity} = \min(0.8500 \times \text{weight}, 0.9900)$ 공식으로 수렴하여 우선 노출 여부를 강제 결정합니다.

### 3) 강력한 위키링크(`[[WikiLink]]`) 연결 법칙
* **기존 토픽 링크**: 본문에 지식베이스 내에 이미 존재하는 토픽 제목(예: `LLM-Wiki`, `PostgreSQL`, `pgvector`)이 언급되면, 반드시 `[[llm-wiki]]`, `[[postgresql]]` 형태로 감싸 위키링크를 엮습니다.
* **양방향 역참조**: Q&A 저널 하단에 `## 관련 주제` 섹션을 두고 `[[토픽명]]`을 나열하며, Topic Summary 문서 업데이트 시 관련 Q&A 저널로 향하는 역링크(`[[HHMM-파일명]]`)를 본문에 녹여 넣습니다.
* **새 토픽 선언**: 중요 개념이나 기술이 처음 등장하면 우선 `[[새개념]]` 링크를 걸고, 즉시 `topics/` 아래에 해당 뼈대 노트를 생성합니다.

### 4) 지식 합성 및 누적 규칙 (Incremental Synthesis)
* **지식 누적 (Append & Refine)**: 기존 내용을 덮어쓰지 않고, 하단에 `### 업데이트 (YYYY-MM-DD)` 섹션을 구성하여 시간 순서대로 지식을 덧붙입니다.
