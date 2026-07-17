import hashlib
import json
import uuid
from typing import Any, Dict, List, Optional

from src.learning.domain.feedback import VALID_ASSESSMENTS, VALID_CONFIDENCE
from src.learning.domain.review import VALID_REVIEW_PRIORITIES


VALID_SCOPES = {"inbox", "knowledge", "combined"}
VALID_EFFECTIVE_SCOPES = VALID_SCOPES | {"none"}
VALID_LEVELS = {"beginner", "practical", "advanced"}
VALID_QUESTION_TYPES = {"diagnostic", "retrieval", "comparison", "counterexample", "application"}
VALID_RELATIONSHIPS = {"confirm", "extend", "conflict", "replace", "unresolved"}
VALID_CANDIDATE_TYPES = {
    "learning_record", "inbox_promotion", "knowledge_correction", "unresolved_question",
}


def _text(value: Any, name: str, limit: int, required: bool = True) -> Optional[str]:
    normalized = str(value or "").strip()
    if required and not normalized:
        raise ValueError(f"{name}은(는) 필수입니다.")
    return normalized[:limit] or None


def _list(values: Optional[List[str]], limit: int = 20) -> List[str]:
    return [str(value).strip()[:500] for value in (values or []) if str(value).strip()][:limit]


class LearningSessionService:
    def __init__(self, repository):
        self.repository = repository

    def start(
        self, owner_id: str, topic: str, requested_scope: str, effective_scope: str,
        goal: str, level: str, duration_minutes: int, first_question: str,
        sources: Optional[List[Dict[str, Any]]] = None,
        client_request_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        requested_scope = (requested_scope or "combined").lower()
        effective_scope = (effective_scope or "none").lower()
        level = (level or "practical").lower()
        if requested_scope not in VALID_SCOPES or effective_scope not in VALID_EFFECTIVE_SCOPES:
            raise ValueError("올바르지 않은 학습 범위입니다.")
        if level not in VALID_LEVELS:
            raise ValueError("올바르지 않은 학습 수준입니다.")
        if not 5 <= duration_minutes <= 120:
            raise ValueError("duration_minutes는 5 이상 120 이하여야 합니다.")
        clean_sources = self._sources(sources or [])
        session_id = str(uuid.uuid4())
        question_id = str(uuid.uuid4())
        snapshot = {
            "schema_version": 1,
            "source_refs": [f"{source['source_type']}:{source['source_ref']}" for source in clean_sources],
        }
        return self.repository.start(owner_id, {
            "session_id": session_id,
            "client_request_id": _text(client_request_id, "client_request_id", 100, False),
            "topic": _text(topic, "topic", 300),
            "requested_scope": requested_scope,
            "effective_scope": effective_scope,
            "goal": _text(goal or "understand", "goal", 500),
            "level": level,
            "duration_minutes": duration_minutes,
            "plan_snapshot": snapshot,
        }, {
            "question_id": question_id,
            "question_type": "diagnostic",
            "prompt": _text(first_question, "first_question", 4000),
            "evidence_refs": [],
        }, clean_sources)

    def record_attempt(
        self, owner_id: str, session_id: str, question_id: str, answer: str,
        assessment: str, confidence: str, feedback_plan: Dict[str, Any],
        missing_concepts: Optional[List[str]] = None,
        misconceptions: Optional[List[str]] = None,
        evidence_refs: Optional[List[str]] = None,
        next_question: Optional[str] = None,
        next_question_type: str = "retrieval",
        next_evidence_refs: Optional[List[str]] = None,
        client_request_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        assessment = (assessment or "").lower()
        confidence = (confidence or "").lower()
        if assessment not in VALID_ASSESSMENTS or confidence not in VALID_CONFIDENCE:
            raise ValueError("올바르지 않은 학습 판정 또는 확신도입니다.")
        if not isinstance(feedback_plan, dict):
            raise ValueError("feedback_plan은 객체여야 합니다.")
        review_schedule = self._review_schedule(feedback_plan)
        question_type = (next_question_type or "retrieval").lower()
        if question_type not in VALID_QUESTION_TYPES:
            raise ValueError("올바르지 않은 다음 질문 유형입니다.")
        follow_up = None
        if next_question and str(next_question).strip():
            follow_up = {
                "question_id": str(uuid.uuid4()),
                "question_type": question_type,
                "prompt": _text(next_question, "next_question", 4000),
                "evidence_refs": _list(next_evidence_refs),
            }
        return self.repository.record_attempt(owner_id, {
            "attempt_id": str(uuid.uuid4()),
            "session_id": self._uuid(session_id, "session_id"),
            "question_id": self._uuid(question_id, "question_id"),
            "client_request_id": _text(client_request_id, "client_request_id", 100, False),
            "answer": _text(answer, "answer", 20000),
            "assessment": assessment,
            "confidence": confidence,
            "missing_concepts": _list(missing_concepts),
            "misconceptions": _list(misconceptions),
            "evidence_refs": _list(evidence_refs),
            "feedback_plan": feedback_plan,
            "review_schedule": review_schedule,
        }, follow_up)

    def resume(self, owner_id: str, session_id: Optional[str] = None) -> Dict[str, Any]:
        return self.repository.resume(owner_id, self._uuid(session_id, "session_id") if session_id else None)

    def complete(self, owner_id: str, session_id: str, summary: Optional[str] = None) -> Dict[str, Any]:
        return self.repository.complete(
            owner_id, self._uuid(session_id, "session_id"), _text(summary, "summary", 4000, False),
        )

    def list_due_reviews(self, owner_id: str, limit: int = 20) -> Dict[str, Any]:
        if limit < 1 or limit > 100:
            raise ValueError("limit은 1 이상 100 이하여야 합니다.")
        reviews = self.repository.list_due_reviews(owner_id, limit)
        return {"reviews": reviews, "count": len(reviews)}

    def record_review(
        self, owner_id: str, review_id: str, answer: str, assessment: str,
        confidence: str, feedback_plan: Dict[str, Any],
        client_request_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        normalized_assessment = (assessment or "").lower()
        normalized_confidence = (confidence or "").lower()
        if normalized_assessment not in VALID_ASSESSMENTS or normalized_confidence not in VALID_CONFIDENCE:
            raise ValueError("올바르지 않은 복습 판정 또는 확신도입니다.")
        if not isinstance(feedback_plan, dict):
            raise ValueError("feedback_plan은 객체여야 합니다.")
        priority = str(feedback_plan.get("review_priority") or "medium").lower()
        if priority not in VALID_REVIEW_PRIORITIES:
            raise ValueError("올바르지 않은 복습 우선순위입니다.")
        return self.repository.record_review(owner_id, {
            "review_id": self._uuid(review_id, "review_id"),
            "answer": _text(answer, "answer", 20000),
            "assessment": normalized_assessment,
            "confidence": normalized_confidence,
            "feedback_plan": feedback_plan,
            "review_priority": priority,
            "client_request_id": _text(client_request_id, "client_request_id", 100, False),
        })

    def prepare_knowledge_candidates(self, owner_id: str, session_id: str) -> Dict[str, Any]:
        normalized_session_id = self._uuid(session_id, "session_id")
        history = self.repository.resume(owner_id, normalized_session_id)
        questions = []
        for question in history["questions"][-50:]:
            questions.append({
                "question_id": question.get("question_id"),
                "question_type": question.get("question_type"),
                "prompt": str(question.get("prompt") or "")[:4000],
                "answer": str(question.get("answer") or "")[:4000] or None,
                "assessment": question.get("assessment"),
                "confidence": question.get("confidence"),
                "missing_concepts": question.get("missing_concepts") or [],
                "misconceptions": question.get("misconceptions") or [],
                "evidence_refs": question.get("attempt_evidence_refs") or question.get("evidence_refs") or [],
            })
        existing = self.repository.list_knowledge_candidates(owner_id, normalized_session_id)
        return {
            "session_id": normalized_session_id,
            "topic": history["session"].get("topic"),
            "status": history["session"].get("status"),
            "sources": history["sources"],
            "learning_history": questions,
            "existing_candidates": existing,
            "candidate_types": sorted(VALID_CANDIDATE_TYPES),
            "client_drafting_contract": {
                "server_llm_used": False,
                "rules": [
                    "후보 하나에는 하나의 독립적인 주장이나 학습 결과만 담는다.",
                    "학습자의 오답 자체를 사실처럼 기록하지 않고 교정된 내용과 근거를 함께 쓴다.",
                    "Inbox 승격은 미검증 출처임을 밝히고 Knowledge 근거와 구분한다.",
                    "knowledge_correction은 기존 문서를 자동 덮어쓰지 않으며 정정 근거를 새 기록으로 제안한다.",
                    "각 후보는 stage 후 사용자에게 개별적으로 보여주고 명시적 승인 또는 거절을 받는다.",
                ],
                "next_tool": "stage_learning_knowledge_candidates",
            },
        }

    def stage_knowledge_candidates(
        self, owner_id: str, session_id: str, candidates: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        normalized_session_id = self._uuid(session_id, "session_id")
        if not candidates or len(candidates) > 10:
            raise ValueError("재지식화 후보는 한 번에 1개 이상 10개 이하이어야 합니다.")
        normalized = []
        for item in candidates:
            candidate_type = str(item.get("candidate_type") or "").lower()
            if candidate_type not in VALID_CANDIDATE_TYPES:
                raise ValueError("올바르지 않은 재지식화 후보 유형입니다.")
            content = _text(item.get("content"), "content", 30000)
            evidence_refs = _list(item.get("evidence_refs"))
            if candidate_type == "knowledge_correction" and not evidence_refs:
                raise ValueError("knowledge_correction 후보에는 evidence_refs가 필요합니다.")
            topic_name = _text(item.get("topic_name"), "topic_name", 256, False)
            topic_update = _text(item.get("topic_update_text"), "topic_update_text", 10000, False)
            if topic_update and not topic_name:
                raise ValueError("topic_update_text를 사용하려면 topic_name이 필요합니다.")
            normalized.append({
                "candidate_id": str(uuid.uuid4()),
                "client_request_id": _text(item.get("client_request_id"), "client_request_id", 100, False),
                "candidate_type": candidate_type,
                "title": _text(item.get("title"), "title", 300),
                "description": _text(item.get("description"), "description", 2000),
                "tags": _list(item.get("tags"), 20),
                "content": content,
                "topic_name": topic_name,
                "topic_update_text": topic_update,
                "evidence_refs": evidence_refs,
                "content_hash": hashlib.sha256(content.encode("utf-8")).hexdigest(),
            })
        staged = self.repository.stage_knowledge_candidates(owner_id, normalized_session_id, normalized)
        return {"candidates": staged, "count": len(staged), "requires_individual_approval": True}

    def review_knowledge_candidate(
        self, owner_id: str, candidate_id: str, approved: bool, note: Optional[str] = None,
    ) -> Dict[str, Any]:
        if not isinstance(approved, bool):
            raise ValueError("approved는 명시적인 boolean 값이어야 합니다.")
        return self.repository.review_knowledge_candidate(
            owner_id, self._uuid(candidate_id, "candidate_id"), approved,
            _text(note, "note", 2000, False),
        )

    @staticmethod
    def _review_schedule(feedback_plan: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        days = feedback_plan.get("suggested_review_days")
        priority = str(feedback_plan.get("review_priority") or "").lower()
        if days is None:
            return None
        if isinstance(days, bool) or not isinstance(days, int) or not 1 <= days <= 365:
            raise ValueError("suggested_review_days는 1 이상 365 이하의 정수여야 합니다.")
        if priority not in VALID_REVIEW_PRIORITIES - {"blocked"}:
            raise ValueError("복습 일정에는 유효한 review_priority가 필요합니다.")
        return {"interval_days": days, "review_priority": priority}

    @staticmethod
    def _uuid(value: str, name: str) -> str:
        try:
            return str(uuid.UUID(str(value)))
        except (ValueError, TypeError, AttributeError) as exc:
            raise ValueError(f"{name}은(는) 유효한 UUID여야 합니다.") from exc

    @staticmethod
    def _sources(sources: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        if len(sources) > 20:
            raise ValueError("학습 출처는 최대 20개까지 저장할 수 있습니다.")
        result = []
        seen = set()
        for source in sources:
            source_type = str(source.get("source_type") or "").lower()
            source_ref = _text(source.get("source_ref"), "source_ref", 1000)
            relationship = str(source.get("relationship") or "").lower() or None
            if source_type not in {"inbox", "knowledge"}:
                raise ValueError("source_type은 inbox 또는 knowledge여야 합니다.")
            if relationship not in VALID_RELATIONSHIPS | {None}:
                raise ValueError("올바르지 않은 출처 관계입니다.")
            key = (source_type, source_ref)
            if key in seen:
                continue
            seen.add(key)
            metadata = source.get("metadata") if isinstance(source.get("metadata"), dict) else {}
            material = json.dumps({"ref": source_ref, "metadata": metadata}, ensure_ascii=False, sort_keys=True)
            result.append({
                "source_type": source_type, "source_ref": source_ref,
                "relationship": relationship,
                "snapshot_hash": hashlib.sha256(material.encode("utf-8")).hexdigest(),
                "metadata": metadata,
            })
        return result
