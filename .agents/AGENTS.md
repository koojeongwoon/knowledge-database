# LLM-Wiki MCP 사용 규칙

이 지침은 LLM-Wiki MCP가 연결된 클라이언트 에이전트가 개인 지식, Inbox, 학습 기록, 업무 기준본을 안전하고 일관되게 사용하기 위한 기본 규칙입니다.

## 1. 검색 우선 원칙

- 사용자의 개인 메모, 프로젝트 이력, 과거 결정, 업무 규칙 또는 기존 학습 기록이 필요한 요청은 답변 전에 반드시 `search_wiki_knowledge`로 검색합니다.
- 일반 상식만으로 충분한 질문까지 무조건 검색하지 않습니다. 개인 지식이나 이전 맥락이 결과를 바꿀 수 있을 때 검색합니다.
- 검색 결과가 없으면 없다고 밝히고 내용을 지어내지 않습니다.
- 검색 응답의 `Search Event ID`는 사용자가 결과 평가를 요청할 때 사용할 수 있도록 유지합니다.

## 2. 일반 지식 기록과 인덱싱

- 사용자가 기록을 요청했거나 대화에서 재사용 가치가 있는 새 지식·노하우·업무 규칙이 확정되면 `commit_new_knowledge`를 사용합니다.
- 저장 전에 제목, 핵심 내용, 근거, 태그와 기존 지식에 합성할지 여부를 분명히 합니다.
- `commit_new_knowledge`는 파일 저장과 durable indexing queue 등록을 함께 수행합니다. 성공 뒤 `run_database_indexing`을 중복 호출하지 않습니다.
- `run_database_indexing(file_paths=[...])`은 MCP 밖에서 직접 수정한 파일을 즉시 반영할 때만, 실제 변경 파일만 지정해 사용합니다.
- queue 등록 실패는 저장 성공과 구분해 알리고, 저장된 경로와 재시도 가능 상태를 숨기지 않습니다.

## 3. Inbox 경계

- Inbox는 읽기 우선의 `unverified` 자료이며 자동으로 Knowledge로 승격하지 않습니다.
- 파일·이미지·링크를 Inbox Markdown으로 저장할 때는 원문을 실제로 읽고 구조화한 결과를 사용자에게 확인받은 뒤 `create_inbox_markdown`을 호출합니다.
- 링크 원문을 읽지 못했다면 URL만으로 내용 저장 성공을 주장하지 않습니다.
- Inbox 자료의 Knowledge 승격은 학습 후보 승인 또는 사용자의 별도 명시적 요청을 거칩니다.

## 4. 학습 시작

- 사용자가 범위를 지정하지 않고 학습을 요청하면 `prepare_learning_session(scope="combined")`을 호출합니다.
- 반환된 `due_review_summary.count`가 1 이상이면 새 학습 전에 복습 항목이 있음을 알리고 먼저 진행할지 묻습니다.
- 새 학습을 진행하면 `first_question` 하나만 제시합니다. 사용자가 답하기 전에 `knowledge_context`, 정답 또는 근거 내용을 노출하지 않습니다.
- 지속 기록이 필요한 학습은 준비 결과로 `start_learning_session`을 호출하고 `session_id`와 `question_id`를 유지합니다.

## 5. 답변 평가와 전이 측정

- 답변 의미 평가는 현재 클라이언트 LLM이 반환된 근거와 비교해 수행합니다. 서버나 클릭 행동을 정답 라벨로 취급하지 않습니다.
- 판정은 `mastered`, `partial`, `misconception`, `unknown`, `unverifiable` 중 하나로 하고 `plan_learning_feedback`으로 정규화합니다.
- `unverifiable` 외 판정에는 최소 하나의 `evidence_refs`가 필요합니다.
- 학습 증거를 다음처럼 구분합니다.
  - 정의나 사실 재현: `learning_dimension="retrieval"`
  - 원리와 인과 설명: `learning_dimension="comprehension"`
  - 새 상황 적용: `learning_dimension="transfer"`, `transfer_level="near"|"far"`
- 실제 지원 수준을 `support_level="none"|"light"|"substantial"`로 기록합니다. 힌트가 있었다면 독립 숙달로 간주하지 않습니다.
- 높은 확신의 실패가 `overconfident`로 반환되면 정답을 바로 넘기기 전에 확신의 근거와 놓친 단서를 비교하게 합니다.
- `record_learning_attempt`에는 사용자의 실제 답변, 판정, 확신도, 정규화된 `feedback_plan`과 선택적 다음 질문을 저장합니다. write 재시도에는 안정적인 `client_request_id`를 사용합니다.

## 6. 세션 완료와 복습

- 세션 종료 전에 `prepare_learning_completion`을 호출합니다.
- `ready_to_complete=false`이면 `next_question_contract`에 따라 부족한 인출·이해·near/far 전이 문제를 이어갑니다.
- `ready_to_complete=true`일 때 `complete_learning_session`을 호출합니다. 완료되지 않은 세션을 완료됐다고 말하지 않습니다.
- 사용자가 복습을 요청하면 `list_due_learning_reviews`를 호출하고 한 문제씩 진행합니다.
- 복습에서는 원 `prompt`를 그대로 반복하지 않고 반환된 `question_contract`에 맞는 새 상황의 지연 전이 문제를 만듭니다.
- 복습 답변도 `plan_learning_feedback`을 transfer 차원과 계약의 near/far 수준으로 호출한 뒤 `record_learning_review`에 저장합니다.

## 7. 학습 결과의 Knowledge 승격

- 학습 결과를 자동으로 Knowledge에 저장하지 않습니다.
- `prepare_learning_knowledge_candidates`로 세션 기록을 준비하고, 클라이언트가 독립 주장 단위의 후보를 작성한 뒤 `stage_learning_knowledge_candidates`로 pending 저장합니다.
- 후보를 사용자에게 하나씩 보여주고 명시적인 승인·거절에만 `review_learning_knowledge_candidate`를 호출합니다.
- `approved=true`인 후보만 `commit_learning_knowledge_candidate`로 저장합니다.
- correction이나 conflict 후보를 기존 문서에 자동 덮어쓰지 않습니다.

## 8. 업무 기준본

- 일반 live 지식과 확정 기준본을 구분합니다.
- `prepare_knowledge_baseline`, `confirm_knowledge_baseline`, 특정 `release_id` 검색 적용, 후속 버전 선택은 각각 사용자의 명시적 요청이 있을 때만 수행합니다.
- 기준본 검색은 `search_knowledge_baseline`으로 지정한 release만 조회합니다. 결과가 없을 때 `search_wiki_knowledge`로 자동 fallback하지 않습니다.
- 새 버전은 기존 release를 수정하지 않고 `base_release_id`를 지정한 새 초안으로 준비합니다. 새 버전 확정 후에도 기존 업무의 release를 자동 전환하지 않습니다.

## 9. 검색 피드백과 소유권

- 검색 결과 평가는 사용자가 직접 정답·오답·no-answer를 판정했을 때만 `submit_search_feedback`으로 저장합니다.
- 클릭, 열기, 복사, 검색 행동을 relevance ground truth로 사용하지 않습니다.
- `owner_id`, storage path 또는 다른 사용자의 release/session ID를 추측하거나 클라이언트 입력으로 만들지 않습니다. 인증된 서버 범위를 따릅니다.
- 도구가 없거나 인증이 실패하면 성공한 것처럼 말하지 말고, 가능한 읽기 전용 확인 후 필요한 설정을 안내합니다.
