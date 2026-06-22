---
name: run_wiki_indexing
description: 로컬 마크다운 지식들을 데이터베이스에 실시간으로 증분 인덱싱하여 AI가 검색(RAG)할 수 있게 만듭니다.
allowed-tools:
  - execute_sql
metadata:
  version: 0.1.0
  author: Antigravity
---

# run_wiki_indexing

사용자가 명시적으로 "인덱싱을 실행해줘", "지식을 반영해줘" 라고 승인/요청하거나, 에이전트가 "저장한 마크다운을 검토하셨으니 이제 데이터베이스에 인덱싱(임베딩 동기화)할까요?"라고 물어보고 승인을 얻었을 때 호출하십시오.

## 1. Parameters (매개변수)

* *없음 (No parameters required)*

## 2. Entrypoint (진입점)

* **Type**: Python Function Call
* **Entrypoint**: `src.interfaces.agent_tool:run_wiki_indexing`
* **Runtime**: `.venv/bin/python`

## 3. Reference CLI (테스트 명령어)

```bash
.venv/bin/python -c "from src.interfaces.agent_tool import run_wiki_indexing; print(run_wiki_indexing())"
```
