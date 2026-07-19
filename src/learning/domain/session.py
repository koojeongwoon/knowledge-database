import hashlib
import json
import uuid
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from src.learning.domain.feedback import VALID_ASSESSMENTS, VALID_CONFIDENCE
from src.learning.domain.ports import UuidFactory
from src.learning.domain.review import VALID_REVIEW_PRIORITIES


VALID_SCOPES = {"inbox", "knowledge", "combined"}
VALID_EFFECTIVE_SCOPES = VALID_SCOPES | {"none"}
VALID_LEVELS = {"beginner", "practical", "advanced"}
VALID_QUESTION_TYPES = {"diagnostic", "retrieval", "comparison", "counterexample", "application"}
VALID_RELATIONSHIPS = {"confirm", "extend", "conflict", "replace", "unresolved"}
VALID_CANDIDATE_TYPES = {"learning_record", "inbox_promotion", "knowledge_correction", "unresolved_question"}


def create_uuid() -> str:
    return str(uuid.uuid4())


def text(value: Any, name: str, limit: int, required: bool = True) -> Optional[str]:
    normalized = str(value or "").strip()
    if required and not normalized:
        raise ValueError(f"{name}은(는) 필수입니다.")
    return normalized[:limit] or None


def string_list(values: Optional[List[str]], limit: int = 20) -> List[str]:
    return [str(value).strip()[:500] for value in (values or []) if str(value).strip()][:limit]


def normalized_uuid(value: str, name: str) -> str:
    try:
        return str(uuid.UUID(str(value)))
    except (ValueError, TypeError, AttributeError) as exc:
        raise ValueError(f"{name}은(는) 유효한 UUID여야 합니다.") from exc


@dataclass(frozen=True)
class StartLearningPlan:
    session: Dict[str, Any]
    question: Dict[str, Any]
    sources: List[Dict[str, Any]]


@dataclass(frozen=True)
class AttemptPlan:
    attempt: Dict[str, Any]
    next_question: Optional[Dict[str, Any]]


class LearningSessionPlanner:
    def __init__(self, uuid_factory: UuidFactory):
        self._uuid_factory = uuid_factory

    def start(self, topic: str, requested_scope: str, effective_scope: str, goal: str, level: str,
              duration_minutes: int, first_question: str, sources: List[Dict[str, Any]],
              client_request_id: Optional[str]) -> StartLearningPlan:
        requested_scope = (requested_scope or "combined").lower()
        effective_scope = (effective_scope or "none").lower()
        level = (level or "practical").lower()
        if requested_scope not in VALID_SCOPES or effective_scope not in VALID_EFFECTIVE_SCOPES:
            raise ValueError("올바르지 않은 학습 범위입니다.")
        if level not in VALID_LEVELS:
            raise ValueError("올바르지 않은 학습 수준입니다.")
        if not 5 <= duration_minutes <= 120:
            raise ValueError("duration_minutes는 5 이상 120 이하여야 합니다.")
        clean_sources = self.sources(sources)
        session = {
            "session_id": self._uuid_factory(), "client_request_id": text(client_request_id, "client_request_id", 100, False),
            "topic": text(topic, "topic", 300), "requested_scope": requested_scope, "effective_scope": effective_scope,
            "goal": text(goal or "understand", "goal", 500), "level": level, "duration_minutes": duration_minutes,
            "plan_snapshot": {"schema_version": 1, "source_refs": [f"{s['source_type']}:{s['source_ref']}" for s in clean_sources]},
        }
        question = {"question_id": self._uuid_factory(), "question_type": "diagnostic",
                    "prompt": text(first_question, "first_question", 4000), "evidence_refs": []}
        return StartLearningPlan(session, question, clean_sources)

    def attempt(self, session_id: str, question_id: str, answer: str, assessment: str, confidence: str,
                feedback_plan: Dict[str, Any], missing_concepts: Optional[List[str]], misconceptions: Optional[List[str]],
                evidence_refs: Optional[List[str]], next_question: Optional[str], next_question_type: str,
                next_evidence_refs: Optional[List[str]], client_request_id: Optional[str]) -> AttemptPlan:
        assessment, confidence = (assessment or "").lower(), (confidence or "").lower()
        if assessment not in VALID_ASSESSMENTS or confidence not in VALID_CONFIDENCE:
            raise ValueError("올바르지 않은 학습 판정 또는 확신도입니다.")
        if not isinstance(feedback_plan, dict):
            raise ValueError("feedback_plan은 객체여야 합니다.")
        question_type = (next_question_type or "retrieval").lower()
        if question_type not in VALID_QUESTION_TYPES:
            raise ValueError("올바르지 않은 다음 질문 유형입니다.")
        follow_up = None
        if next_question and str(next_question).strip():
            follow_up = {"question_id": self._uuid_factory(), "question_type": question_type,
                         "prompt": text(next_question, "next_question", 4000), "evidence_refs": string_list(next_evidence_refs)}
        attempt = {
            "attempt_id": self._uuid_factory(), "session_id": normalized_uuid(session_id, "session_id"),
            "question_id": normalized_uuid(question_id, "question_id"),
            "client_request_id": text(client_request_id, "client_request_id", 100, False), "answer": text(answer, "answer", 20000),
            "assessment": assessment, "confidence": confidence, "missing_concepts": string_list(missing_concepts),
            "misconceptions": string_list(misconceptions), "evidence_refs": string_list(evidence_refs),
            "feedback_plan": feedback_plan, "review_schedule": self.review_schedule(feedback_plan),
        }
        return AttemptPlan(attempt, follow_up)

    def candidates(self, candidates: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        if not candidates or len(candidates) > 10:
            raise ValueError("재지식화 후보는 한 번에 1개 이상 10개 이하이어야 합니다.")
        result = []
        for item in candidates:
            candidate_type = str(item.get("candidate_type") or "").lower()
            if candidate_type not in VALID_CANDIDATE_TYPES:
                raise ValueError("올바르지 않은 재지식화 후보 유형입니다.")
            content = text(item.get("content"), "content", 30000)
            evidence_refs = string_list(item.get("evidence_refs"))
            if candidate_type == "knowledge_correction" and not evidence_refs:
                raise ValueError("knowledge_correction 후보에는 evidence_refs가 필요합니다.")
            topic_name = text(item.get("topic_name"), "topic_name", 256, False)
            topic_update = text(item.get("topic_update_text"), "topic_update_text", 10000, False)
            if topic_update and not topic_name:
                raise ValueError("topic_update_text를 사용하려면 topic_name이 필요합니다.")
            result.append({"candidate_id": self._uuid_factory(), "client_request_id": text(item.get("client_request_id"), "client_request_id", 100, False),
                           "candidate_type": candidate_type, "title": text(item.get("title"), "title", 300),
                           "description": text(item.get("description"), "description", 2000), "tags": string_list(item.get("tags"), 20),
                           "content": content, "topic_name": topic_name, "topic_update_text": topic_update, "evidence_refs": evidence_refs,
                           "content_hash": hashlib.sha256(content.encode("utf-8")).hexdigest()})
        return result

    @staticmethod
    def review_schedule(feedback_plan: Dict[str, Any]) -> Optional[Dict[str, Any]]:
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
    def sources(sources: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        if len(sources) > 20:
            raise ValueError("학습 출처는 최대 20개까지 저장할 수 있습니다.")
        result, seen = [], set()
        for source in sources:
            source_type = str(source.get("source_type") or "").lower()
            source_ref = text(source.get("source_ref"), "source_ref", 1000)
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
            result.append({"source_type": source_type, "source_ref": source_ref, "relationship": relationship,
                           "snapshot_hash": hashlib.sha256(material.encode("utf-8")).hexdigest(), "metadata": metadata})
        return result
