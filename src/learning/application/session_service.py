from typing import Any, Dict, List, Optional

from src.learning.domain.feedback import VALID_ASSESSMENTS, VALID_CONFIDENCE
from src.learning.domain.completion import LearningCompletionPolicy
from src.learning.domain.calibration import calibration_signal
from src.learning.domain.ports import LearningSessionRepositoryPort, UuidFactory
from src.learning.domain.review import VALID_REVIEW_PRIORITIES, delayed_transfer_question_contract
from src.learning.domain.session import (
    VALID_CANDIDATE_TYPES, LearningSessionPlanner, create_uuid, normalized_uuid, text,
)


class LearningSessionService:
    def __init__(self, repository: LearningSessionRepositoryPort, uuid_factory: UuidFactory = create_uuid):
        self.repository = repository
        self.planner = LearningSessionPlanner(uuid_factory)

    def start(
        self, owner_id: str, topic: str, requested_scope: str, effective_scope: str,
        goal: str, level: str, duration_minutes: int, first_question: str,
        sources: Optional[List[Dict[str, Any]]] = None,
        client_request_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        plan = self.planner.start(topic, requested_scope, effective_scope, goal, level, duration_minutes,
                                  first_question, sources or [], client_request_id)
        return self.repository.start(owner_id, plan.session, plan.question, plan.sources)

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
        next_transfer_level: str = "none",
    ) -> Dict[str, Any]:
        plan = self.planner.attempt(session_id, question_id, answer, assessment, confidence, feedback_plan,
                                    missing_concepts, misconceptions, evidence_refs, next_question,
                                    next_question_type, next_evidence_refs, client_request_id, next_transfer_level)
        return self.repository.record_attempt(owner_id, plan.attempt, plan.next_question)

    def resume(self, owner_id: str, session_id: Optional[str] = None) -> Dict[str, Any]:
        return self.repository.resume(owner_id, normalized_uuid(session_id, "session_id") if session_id else None)

    def complete(self, owner_id: str, session_id: str, summary: Optional[str] = None) -> Dict[str, Any]:
        normalized_session_id = normalized_uuid(session_id, "session_id")
        readiness = self.prepare_completion(owner_id, normalized_session_id)
        if readiness["completion_gate_enabled"] and not readiness["ready_to_complete"]:
            return {
                "session_id": normalized_session_id, "status": "active", "completed": False,
                "completion_readiness": readiness,
            }
        result = self.repository.complete(
            owner_id, normalized_session_id, text(summary, "summary", 4000, False),
        )
        return {**result, "completed": result.get("status") == "completed", "completion_readiness": readiness}

    def prepare_completion(self, owner_id: str, session_id: str) -> Dict[str, Any]:
        normalized_session_id = normalized_uuid(session_id, "session_id")
        stored = self.repository.completion_evidence(owner_id, normalized_session_id)
        snapshot = stored["session"].get("plan_snapshot") or {}
        schema_version = int(snapshot.get("schema_version") or 1)
        readiness = LearningCompletionPolicy.evaluate(stored["evidence_rows"])
        return {
            "session_id": normalized_session_id,
            "status": stored["session"].get("status"),
            "completion_gate_enabled": schema_version >= 2,
            **readiness,
        }

    def list_due_reviews(self, owner_id: str, limit: int = 20) -> Dict[str, Any]:
        if limit < 1 or limit > 100:
            raise ValueError("limit은 1 이상 100 이하여야 합니다.")
        reviews = self.repository.list_due_reviews(owner_id, limit)
        for review in reviews:
            review["question_contract"] = delayed_transfer_question_contract(review)
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
        evidence = feedback_plan.get("learning_evidence") or {}
        if not isinstance(evidence, dict):
            raise ValueError("feedback_plan.learning_evidence는 객체여야 합니다.")
        dimension = str(evidence.get("dimension") or "").lower()
        transfer_level = str(evidence.get("transfer_level") or "").lower()
        support_level = str(evidence.get("support_level") or "none").lower()
        if dimension != "transfer" or transfer_level not in {"near", "far"}:
            raise ValueError("복습은 near 또는 far transfer 학습 증거로 기록해야 합니다.")
        if support_level not in {"none", "light", "substantial"}:
            raise ValueError("올바르지 않은 복습 지원 수준입니다.")
        priority = str(feedback_plan.get("review_priority") or "medium").lower()
        if priority not in VALID_REVIEW_PRIORITIES:
            raise ValueError("올바르지 않은 복습 우선순위입니다.")
        return self.repository.record_review(owner_id, {
            "review_id": normalized_uuid(review_id, "review_id"),
            "answer": text(answer, "answer", 20000),
            "assessment": normalized_assessment,
            "confidence": normalized_confidence,
            "feedback_plan": feedback_plan,
            "learning_dimension": dimension,
            "transfer_level": transfer_level,
            "support_level": support_level,
            "independent_success": normalized_assessment == "mastered" and support_level == "none",
            "calibration_signal": calibration_signal(
                normalized_assessment, normalized_confidence, support_level,
            ),
            "review_priority": priority,
            "client_request_id": text(client_request_id, "client_request_id", 100, False),
        })

    def prepare_knowledge_candidates(self, owner_id: str, session_id: str) -> Dict[str, Any]:
        normalized_session_id = normalized_uuid(session_id, "session_id")
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
        normalized_session_id = normalized_uuid(session_id, "session_id")
        normalized = self.planner.candidates(candidates)
        staged = self.repository.stage_knowledge_candidates(owner_id, normalized_session_id, normalized)
        return {"candidates": staged, "count": len(staged), "requires_individual_approval": True}

    def review_knowledge_candidate(
        self, owner_id: str, candidate_id: str, approved: bool, note: Optional[str] = None,
    ) -> Dict[str, Any]:
        if not isinstance(approved, bool):
            raise ValueError("approved는 명시적인 boolean 값이어야 합니다.")
        return self.repository.review_knowledge_candidate(
            owner_id, normalized_uuid(candidate_id, "candidate_id"), approved,
            text(note, "note", 2000, False),
        )
