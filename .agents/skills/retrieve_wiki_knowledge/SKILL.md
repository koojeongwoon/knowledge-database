---
name: retrieve_wiki_knowledge
description: 개인 지식베이스(옵시디언 위키)에서 과거 Q&A 저널 및 요약 토픽을 조회하여 지식을 참조합니다.
allowed-tools:
  - execute_sql
metadata:
  version: 0.1.0
  author: Antigravity
---

# retrieve_wiki_knowledge

사용자가 과거 대화 기록, 개발 환경 설정 의사결정 사항, 지식베이스의 개념 정리 등에 관해 물어볼 때 호출하십시오. 최신 의사결정 기록이나 합의를 조회해야 할 때 최우선으로 사용해야 합니다.

## 1. Parameters (매개변수)

* `query` (string, Required): 과거 기록에서 조회할 구체적인 질문 또는 핵심 검색 키워드 (예: '쿠버네티스 서비스 타입', '인덱싱 주기')
* `limit` (integer, Optional, Default: 3): 가져올 참고 문서 조각의 수

## 2. Entrypoint (진입점)

* **Type**: Python Function Call
* **Entrypoint**: `src.api.agent_tool:retrieve_wiki_knowledge`
* **Runtime**: `.venv/bin/python`

## 3. Reference CLI (테스트 명령어)

```bash
.venv/bin/python -c "from src.api.agent_tool import retrieve_wiki_knowledge; print(retrieve_wiki_knowledge('Graph RAG', limit=1))"
```
