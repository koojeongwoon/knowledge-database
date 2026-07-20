import re
import uuid
from typing import Any, Callable, Dict, List, Optional

from src.settings.inbox import InboxService


VALID_SCOPES = {"inbox", "knowledge", "combined"}
VALID_LEVELS = {"beginner", "practical", "advanced"}
MAX_INBOX_SOURCES = 3
MAX_INBOX_EXCERPT_CHARS = 12000
MAX_TOTAL_INBOX_CHARS = 30000


def _tokens(value: str) -> List[str]:
    return list(dict.fromkeys(re.findall(r"[0-9A-Za-z가-힣]{2,}", (value or "").lower())))


def _candidate_score(topic: str, item: Dict[str, Any]) -> int:
    source = item.get("source") or {}
    haystack = " ".join(
        str(value or "")
        for value in (
            item.get("title"),
            item.get("note"),
            item.get("filename"),
            item.get("url"),
            source.get("original_filename"),
            source.get("original_url"),
        )
    ).lower()
    normalized_topic = (topic or "").strip().lower()
    score = 10 if normalized_topic and normalized_topic in haystack else 0
    score += sum(1 for token in _tokens(topic) if token in haystack)
    return score


class LearningPreparationService:
    def __init__(
        self,
        inbox_service: InboxService,
        knowledge_search: Callable[[str, int], str],
    ):
        self.inbox_service = inbox_service
        self.knowledge_search = knowledge_search

    def prepare(
        self,
        topic: str,
        scope: str = "combined",
        goal: str = "understand",
        level: str = "practical",
        duration_minutes: int = 20,
        inbox_item_ids: Optional[List[str]] = None,
        knowledge_limit: int = 5,
    ) -> Dict[str, Any]:
        normalized_topic = (topic or "").strip()
        normalized_scope = (scope or "combined").strip().lower()
        normalized_level = (level or "practical").strip().lower()
        normalized_goal = (goal or "understand").strip()
        if not normalized_topic:
            raise ValueError("학습 주제(topic)는 필수입니다.")
        if normalized_scope not in VALID_SCOPES:
            raise ValueError("scope는 inbox, knowledge, combined 중 하나여야 합니다.")
        if normalized_level not in VALID_LEVELS:
            raise ValueError("level은 beginner, practical, advanced 중 하나여야 합니다.")
        if duration_minutes < 5 or duration_minutes > 120:
            raise ValueError("duration_minutes는 5 이상 120 이하여야 합니다.")
        if knowledge_limit < 1 or knowledge_limit > 10:
            raise ValueError("knowledge_limit은 1 이상 10 이하여야 합니다.")

        inbox_sources = []
        if normalized_scope in ("inbox", "combined"):
            inbox_sources = self._load_inbox_sources(normalized_topic, inbox_item_ids or [])

        knowledge_context = None
        if normalized_scope in ("knowledge", "combined"):
            knowledge_context = self.knowledge_search(normalized_topic, knowledge_limit)
            if knowledge_context and "관련된 문서를 찾지 못했습니다" in knowledge_context:
                knowledge_context = None

        has_inbox = bool(inbox_sources)
        has_knowledge = bool(knowledge_context)
        if has_inbox and has_knowledge:
            effective_scope = "combined"
        elif has_inbox:
            effective_scope = "inbox"
        elif has_knowledge:
            effective_scope = "knowledge"
        else:
            effective_scope = "none"

        warnings = []
        if normalized_scope in ("inbox", "combined") and not has_inbox:
            warnings.append("관련 Inbox 자료를 찾지 못했습니다.")
        if normalized_scope in ("knowledge", "combined") and not has_knowledge:
            warnings.append("관련 Knowledge 근거를 찾지 못했습니다.")

        first_question = (
            f"자료를 보지 않고 '{normalized_topic}'에 대해 현재 알고 있는 내용을 설명해 주세요. "
            f"핵심 개념, 작동 원리, 실제 적용 조건을 포함하고 확신이 낮은 부분도 표시해 주세요."
        )
        return {
            "session_plan_id": uuid.uuid4().hex,
            "requested_scope": normalized_scope,
            "effective_scope": effective_scope,
            "topic": normalized_topic,
            "goal": normalized_goal[:500],
            "level": normalized_level,
            "duration_minutes": duration_minutes,
            "source_summary": {
                "inbox_count": len(inbox_sources),
                "knowledge_available": has_knowledge,
            },
            "inbox_sources": inbox_sources,
            "knowledge_context": knowledge_context,
            "relationship_categories": ["confirm", "extend", "conflict", "replace", "unresolved"],
            "assessment_states": ["mastered", "partial", "misconception", "unknown", "unverifiable"],
            "client_assessment_contract": {
                "tool": "plan_learning_feedback",
                "required": ["assessment", "confidence", "evidence_refs"],
                "conditional": {
                    "partial": ["missing_concepts 또는 hint"],
                    "misconception": ["misconceptions 또는 hint"],
                    "unverifiable": ["evidence_refs 생략 가능"],
                },
                "rule": "의미 판정은 클라이언트 LLM이 수행하고 서버는 별도 LLM을 호출하지 않는다.",
            },
            "learning_evidence_protocol": [
                "정의 재현은 retrieval, 원리 설명은 comprehension, 새 상황 적용은 transfer로 구분한다.",
                "transfer는 유사한 변형인 near와 맥락이 달라진 far를 구분한다.",
                "힌트 없이 성공한 경우만 independent_success로 본다.",
                "retrieval 숙달만으로 학습 완료를 주장하지 않고 application 질문으로 전이를 확인한다.",
            ],
            "first_question": first_question if effective_scope != "none" else None,
            "tutor_protocol": [
                "처음에는 first_question 하나만 사용자에게 제시한다.",
                "사용자가 답하기 전에는 knowledge_context와 Inbox 근거의 정답 내용을 노출하지 않는다.",
                "답변 뒤 근거와 비교해 누락, 오개념, 불확실성을 판정한다.",
                "정답을 바로 말하기 전에 한 단계씩 힌트를 제공한다.",
                "Inbox는 unverified 자료이므로 Knowledge와 충돌하면 자동 대체하지 않는다.",
                "답변을 평가한 뒤 plan_learning_feedback으로 피드백 순서와 복습 우선순위를 정규화한다.",
            ],
            "warnings": warnings,
        }

    def _load_inbox_sources(self, topic: str, item_ids: List[str]) -> List[Dict[str, Any]]:
        if item_ids:
            selected_ids = list(dict.fromkeys(item_ids))[:MAX_INBOX_SOURCES]
        else:
            ranked = [
                (_candidate_score(topic, item), item)
                for item in self.inbox_service.list_items()
            ]
            ranked = [pair for pair in ranked if pair[0] > 0]
            ranked.sort(key=lambda pair: (pair[0], pair[1].get("created_at", "")), reverse=True)
            selected_ids = [item["id"] for _, item in ranked[:MAX_INBOX_SOURCES]]

        sources = []
        remaining = MAX_TOTAL_INBOX_CHARS
        for item_id in selected_ids:
            result = self.inbox_service.read_for_learning(item_id)
            item = result["item"]
            content = result.get("content")
            excerpt = None
            truncated = False
            if content and remaining > 0:
                allowed = min(MAX_INBOX_EXCERPT_CHARS, remaining)
                excerpt = content[:allowed]
                truncated = len(content) > allowed
                remaining -= len(excerpt)
            sources.append({
                "item_id": item.get("id"),
                "title": item.get("title"),
                "subtype": item.get("subtype"),
                "authority": item.get("authority", "unverified"),
                "source": item.get("source"),
                "content_status": result.get("content_status"),
                "content_excerpt": excerpt,
                "truncated": truncated,
            })
        return sources
