import json
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional

from src.api.exceptions import InvalidArgumentException, WikiBaseException
from src.learning.application.session_service import LearningSessionService
from src.learning.domain.session import normalized_uuid


@dataclass(frozen=True)
class LearningSessionApiHandler:
    service_factory: Callable[[], LearningSessionService]

    def _execute(self, operation: Callable[[LearningSessionService], Dict[str, Any]]) -> Dict[str, Any]:
        service = self.service_factory()
        try:
            return operation(service)
        except (ValueError, KeyError) as exc:
            raise InvalidArgumentException(str(exc)) from exc
        finally:
            service.repository.db_manager.close()

    def start(self, owner_id: str, topic: str, requested_scope: str, effective_scope: str,
              goal: str, level: str, duration_minutes: int, first_question: str,
              sources: Optional[List[Dict[str, Any]]], client_request_id: Optional[str]) -> Dict[str, Any]:
        return self._execute(lambda service: service.start(
            owner_id, topic, requested_scope, effective_scope, goal, level,
            duration_minutes, first_question, sources, client_request_id,
        ))

    def record_attempt(self, owner_id: str, session_id: str, question_id: str, answer: str,
                       assessment: str, confidence: str, feedback_plan: Dict[str, Any],
                       missing_concepts: Optional[List[str]], misconceptions: Optional[List[str]],
                       evidence_refs: Optional[List[str]], next_question: Optional[str],
                       next_question_type: str, next_evidence_refs: Optional[List[str]],
                       client_request_id: Optional[str], next_transfer_level: str) -> Dict[str, Any]:
        return self._execute(lambda service: service.record_attempt(
            owner_id, session_id, question_id, answer, assessment, confidence, feedback_plan,
            missing_concepts, misconceptions, evidence_refs, next_question, next_question_type,
            next_evidence_refs, client_request_id,
            next_transfer_level,
        ))

    def resume(self, owner_id: str, session_id: Optional[str]) -> Dict[str, Any]:
        return self._execute(lambda service: service.resume(owner_id, session_id))

    def complete(self, owner_id: str, session_id: str, summary: Optional[str]) -> Dict[str, Any]:
        return self._execute(lambda service: service.complete(owner_id, session_id, summary))

    def prepare_completion(self, owner_id: str, session_id: str) -> Dict[str, Any]:
        return self._execute(lambda service: service.prepare_completion(owner_id, session_id))

    def list_due_reviews(self, owner_id: str, limit: int) -> Dict[str, Any]:
        return self._execute(lambda service: service.list_due_reviews(owner_id, limit))

    def record_review(self, owner_id: str, review_id: str, answer: str, assessment: str,
                      confidence: str, feedback_plan: Dict[str, Any],
                      client_request_id: Optional[str]) -> Dict[str, Any]:
        return self._execute(lambda service: service.record_review(
            owner_id, review_id, answer, assessment, confidence, feedback_plan, client_request_id,
        ))

    def prepare_candidates(self, owner_id: str, session_id: str) -> Dict[str, Any]:
        return self._execute(lambda service: service.prepare_knowledge_candidates(owner_id, session_id))

    def stage_candidates(self, owner_id: str, session_id: str,
                         candidates: List[Dict[str, Any]]) -> Dict[str, Any]:
        return self._execute(lambda service: service.stage_knowledge_candidates(owner_id, session_id, candidates))

    def review_candidate(self, owner_id: str, candidate_id: str, approved: bool,
                         note: Optional[str]) -> Dict[str, Any]:
        return self._execute(lambda service: service.review_knowledge_candidate(
            owner_id, candidate_id, approved, note,
        ))


@dataclass(frozen=True)
class LearningCandidateCommitHandler:
    service_factory: Callable[[], LearningSessionService]
    commit_knowledge: Callable[..., str]

    def commit(self, owner_id: str, candidate_id: str) -> Dict[str, Any]:
        service = self.service_factory()
        normalized_id = normalized_uuid(candidate_id, "candidate_id")
        claimed = False
        write_succeeded = False
        try:
            candidate = service.repository.claim_approved_knowledge_candidate(owner_id, normalized_id)
            if candidate["status"] == "committed":
                return {
                    "candidate_id": normalized_id,
                    "status": "committed",
                    "qa_file_path": candidate.get("qa_file_path"),
                    "topic_file_path": candidate.get("topic_file_path"),
                    "idempotent_replay": True,
                }
            claimed = True
            response = json.loads(self.commit_knowledge(
                title=candidate["title"],
                description=candidate["description"],
                tags=candidate["tags"],
                content=candidate["content"],
                topic_name=candidate.get("topic_name"),
                topic_update_text=candidate.get("topic_update_text"),
                visibility="private",
            ))
            if not response.get("success"):
                raise WikiBaseException(response.get("message") or "승인된 후보의 Knowledge 저장에 실패했습니다.")
            write_succeeded = True
            data = response.get("data") or {}
            receipt = service.repository.mark_knowledge_candidate_committed(
                owner_id, normalized_id, data["qa_file_path"], data.get("topic_file_path"),
            )
            return {"candidate": receipt, "knowledge_commit": data}
        except (ValueError, KeyError) as exc:
            raise InvalidArgumentException(str(exc)) from exc
        finally:
            if claimed and not write_succeeded:
                service.repository.release_knowledge_candidate_claim(owner_id, normalized_id)
            service.repository.db_manager.close()
