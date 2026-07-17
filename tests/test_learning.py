import unittest
import json
from unittest.mock import Mock, patch

from src.api.agent_tool import commit_wiki_learning_knowledge_candidate
from src.core.config import current_user_config
from src.learning.application.service import LearningPreparationService
from src.learning.application.session_service import LearningSessionService
from src.learning.domain.feedback import LearningFeedbackPlanner
from src.learning.domain.review import next_review_interval


class LearningPreparationServiceTests(unittest.TestCase):
    def test_combined_is_default_and_returns_first_question(self):
        inbox = Mock()
        inbox.list_items.return_value = [{
            "id": "a" * 32,
            "title": "OAuth 보안 문서",
            "created_at": "2026-01-01",
        }]
        inbox.read_for_learning.return_value = {
            "item": {
                "id": "a" * 32,
                "title": "OAuth 보안 문서",
                "subtype": "derived_markdown",
                "authority": "unverified",
            },
            "content_status": "available",
            "content": "## PKCE",
        }
        service = LearningPreparationService(inbox, lambda query, limit: "<document>OAuth Knowledge</document>")

        result = service.prepare("OAuth")

        self.assertEqual(result["requested_scope"], "combined")
        self.assertEqual(result["effective_scope"], "combined")
        self.assertEqual(result["source_summary"]["inbox_count"], 1)
        self.assertIn("자료를 보지 않고", result["first_question"])
        self.assertIn("정답 내용을 노출하지 않는다", " ".join(result["tutor_protocol"]))
        self.assertEqual(result["client_assessment_contract"]["tool"], "plan_learning_feedback")

    def test_combined_falls_back_to_knowledge_when_inbox_has_no_match(self):
        inbox = Mock()
        inbox.list_items.return_value = [{"id": "b" * 32, "title": "금리 메모"}]
        service = LearningPreparationService(inbox, lambda query, limit: "<document>OAuth</document>")

        result = service.prepare("OAuth")

        self.assertEqual(result["effective_scope"], "knowledge")
        self.assertIn("Inbox", " ".join(result["warnings"]))
        inbox.read_for_learning.assert_not_called()

    def test_explicit_inbox_ids_are_used_without_title_matching(self):
        item_id = "c" * 32
        inbox = Mock()
        inbox.read_for_learning.return_value = {
            "item": {"id": item_id, "title": "직접 선택", "authority": "unverified"},
            "content_status": "available",
            "content": "선택된 내용",
        }
        service = LearningPreparationService(inbox, lambda query, limit: "unused")

        result = service.prepare("OAuth", scope="inbox", inbox_item_ids=[item_id])

        self.assertEqual(result["effective_scope"], "inbox")
        self.assertEqual(result["inbox_sources"][0]["item_id"], item_id)
        inbox.list_items.assert_not_called()

    def test_no_sources_returns_no_first_question(self):
        inbox = Mock()
        inbox.list_items.return_value = []
        service = LearningPreparationService(
            inbox,
            lambda query, limit: "지식베이스에서 관련된 문서를 찾지 못했습니다.",
        )

        result = service.prepare("없는 주제")

        self.assertEqual(result["effective_scope"], "none")
        self.assertIsNone(result["first_question"])

    def test_invalid_options_are_rejected(self):
        service = LearningPreparationService(Mock(), lambda query, limit: "")
        with self.assertRaises(ValueError):
            service.prepare("OAuth", scope="all")
        with self.assertRaises(ValueError):
            service.prepare("OAuth", duration_minutes=1)

    @patch("src.learning.application.service.uuid.uuid4")
    def test_session_plan_id_is_ephemeral_uuid(self, uuid4):
        uuid4.return_value.hex = "plan-id"
        inbox = Mock()
        inbox.list_items.return_value = []
        service = LearningPreparationService(inbox, lambda query, limit: "<document>Knowledge</document>")
        self.assertEqual(service.prepare("OAuth")["session_plan_id"], "plan-id")


class LearningFeedbackPlannerTests(unittest.TestCase):
    def test_mastered_advances_without_server_llm(self):
        result = LearningFeedbackPlanner().plan(
            assessment="mastered",
            confidence="high",
            evidence_refs=["topics/oauth.md"],
            next_question="새 상황에 적용해 보세요.",
        )

        self.assertFalse(result["server_llm_used"])
        self.assertEqual(result["assessment_source"], "client_llm")
        self.assertEqual(result["next_action"], "advance")
        self.assertFalse(result["should_reask"])
        self.assertEqual(result["suggested_review_days"], 7)

    def test_partial_requires_gap_and_reasks(self):
        result = LearningFeedbackPlanner().plan(
            assessment="partial",
            confidence="medium",
            missing_concepts=["code_verifier"],
            evidence_refs=["qa/pkce.md"],
        )

        self.assertEqual(result["next_action"], "hint_then_retry")
        self.assertTrue(result["should_reask"])
        self.assertIn("code_verifier", result["hint"])
        self.assertEqual(result["suggested_review_days"], 3)

    def test_high_confidence_misconception_is_critical(self):
        result = LearningFeedbackPlanner().plan(
            assessment="misconception",
            confidence="high",
            misconceptions=["PKCE가 client secret을 대체한다"],
            evidence_refs=["topics/oauth.md"],
            hint="공개 클라이언트가 보관할 수 없는 값을 생각해 보세요.",
        )

        self.assertEqual(result["review_priority"], "critical")
        self.assertEqual(result["suggested_review_days"], 1)

    def test_unverifiable_requires_no_evidence(self):
        result = LearningFeedbackPlanner().plan(assessment="unverifiable", confidence="low")
        self.assertEqual(result["next_action"], "request_better_evidence")
        self.assertEqual(result["review_priority"], "blocked")

    def test_non_unverifiable_requires_evidence(self):
        with self.assertRaisesRegex(ValueError, "evidence_refs"):
            LearningFeedbackPlanner().plan(assessment="unknown")

    def test_partial_requires_missing_concepts_or_hint(self):
        with self.assertRaisesRegex(ValueError, "missing_concepts"):
            LearningFeedbackPlanner().plan(
                assessment="partial",
                evidence_refs=["topics/oauth.md"],
            )


class LearningSessionServiceTests(unittest.TestCase):
    def test_start_stores_only_source_reference_snapshot(self):
        repository = Mock()
        repository.start.return_value = {"session_id": "created", "status": "active"}
        service = LearningSessionService(repository)

        result = service.start(
            owner_id="owner", topic="OAuth", requested_scope="combined",
            effective_scope="combined", goal="understand", level="practical",
            duration_minutes=20, first_question="설명해 보세요.",
            sources=[{
                "source_type": "inbox", "source_ref": "item-1",
                "relationship": "extend", "metadata": {"title": "메모"},
            }], client_request_id="request-1",
        )

        self.assertEqual(result["status"], "active")
        owner_id, session, question, sources = repository.start.call_args.args
        self.assertEqual(owner_id, "owner")
        self.assertEqual(question["question_type"], "diagnostic")
        self.assertEqual(sources[0]["source_ref"], "item-1")
        self.assertEqual(len(sources[0]["snapshot_hash"]), 64)
        self.assertEqual(session["plan_snapshot"]["source_refs"], ["inbox:item-1"])
        self.assertNotIn("content", session["plan_snapshot"])

    def test_record_attempt_passes_client_assessment_and_next_question(self):
        repository = Mock()
        repository.record_attempt.return_value = {"attempt_id": "created"}
        service = LearningSessionService(repository)
        session_id = "11111111-1111-4111-8111-111111111111"
        question_id = "22222222-2222-4222-8222-222222222222"

        service.record_attempt(
            "owner", session_id, question_id, "내 답변", "partial", "medium",
            {"next_action": "hint_then_retry"}, missing_concepts=["nonce"],
            evidence_refs=["topics/oauth.md"], next_question="다시 설명해 보세요.",
            client_request_id="attempt-1",
        )

        owner_id, attempt, next_question = repository.record_attempt.call_args.args
        self.assertEqual(owner_id, "owner")
        self.assertEqual(attempt["assessment"], "partial")
        self.assertEqual(attempt["feedback_plan"]["next_action"], "hint_then_retry")
        self.assertIsNone(attempt["review_schedule"])
        self.assertEqual(next_question["question_type"], "retrieval")

    def test_resume_and_complete_reject_invalid_session_uuid(self):
        service = LearningSessionService(Mock())
        with self.assertRaisesRegex(ValueError, "UUID"):
            service.resume("owner", "not-a-uuid")
        with self.assertRaisesRegex(ValueError, "UUID"):
            service.complete("owner", "not-a-uuid")

    def test_start_validates_source_relationship(self):
        service = LearningSessionService(Mock())
        with self.assertRaisesRegex(ValueError, "출처 관계"):
            service.start(
                "owner", "OAuth", "combined", "combined", "understand", "practical", 20,
                "질문", [{"source_type": "knowledge", "source_ref": "qa/a.md", "relationship": "trust"}],
            )

    def test_attempt_feedback_creates_review_schedule(self):
        repository = Mock()
        repository.record_attempt.return_value = {"attempt_id": "created"}
        service = LearningSessionService(repository)

        service.record_attempt(
            "owner", "11111111-1111-4111-8111-111111111111",
            "22222222-2222-4222-8222-222222222222", "답변", "misconception", "high",
            {"review_priority": "critical", "suggested_review_days": 1},
        )

        attempt = repository.record_attempt.call_args.args[1]
        self.assertEqual(attempt["review_schedule"], {
            "interval_days": 1, "review_priority": "critical",
        })

    def test_list_due_reviews_validates_limit(self):
        service = LearningSessionService(Mock())
        with self.assertRaisesRegex(ValueError, "limit"):
            service.list_due_reviews("owner", 0)

    def test_record_review_validates_and_delegates(self):
        repository = Mock()
        repository.record_review.return_value = {"next_interval_days": 6}
        service = LearningSessionService(repository)

        result = service.record_review(
            "owner", "33333333-3333-4333-8333-333333333333", "복습 답변",
            "mastered", "high", {"review_priority": "low"}, "review-request-1",
        )

        self.assertEqual(result["next_interval_days"], 6)
        review = repository.record_review.call_args.args[1]
        self.assertEqual(review["assessment"], "mastered")
        self.assertEqual(review["client_request_id"], "review-request-1")

    def test_prepare_knowledge_candidates_returns_client_drafting_pack(self):
        repository = Mock()
        repository.resume.return_value = {
            "session": {"topic": "OAuth", "status": "completed"},
            "sources": [{"source_type": "knowledge", "source_ref": "topics/oauth.md"}],
            "questions": [{
                "question_id": "q1", "question_type": "diagnostic", "prompt": "설명",
                "answer": "답", "assessment": "partial", "confidence": "medium",
                "missing_concepts": ["nonce"], "attempt_evidence_refs": ["topics/oauth.md"],
            }],
        }
        repository.list_knowledge_candidates.return_value = []
        service = LearningSessionService(repository)

        result = service.prepare_knowledge_candidates(
            "owner", "11111111-1111-4111-8111-111111111111",
        )

        self.assertFalse(result["client_drafting_contract"]["server_llm_used"])
        self.assertEqual(result["learning_history"][0]["assessment"], "partial")
        self.assertIn("knowledge_correction", result["candidate_types"])

    def test_stage_candidates_requires_evidence_for_correction(self):
        service = LearningSessionService(Mock())
        with self.assertRaisesRegex(ValueError, "evidence_refs"):
            service.stage_knowledge_candidates(
                "owner", "11111111-1111-4111-8111-111111111111", [{
                    "candidate_type": "knowledge_correction", "title": "정정",
                    "description": "설명", "content": "정정 내용",
                }],
            )

    def test_stage_candidates_are_pending_and_individually_approved(self):
        repository = Mock()
        repository.stage_knowledge_candidates.return_value = [{
            "candidate_id": "candidate", "status": "pending",
        }]
        service = LearningSessionService(repository)

        result = service.stage_knowledge_candidates(
            "owner", "11111111-1111-4111-8111-111111111111", [{
                "candidate_type": "learning_record", "title": "학습 결과",
                "description": "세션에서 정리", "content": "교정된 지식",
                "tags": ["oauth"], "evidence_refs": ["topics/oauth.md"],
                "client_request_id": "candidate-request-1",
            }],
        )

        self.assertTrue(result["requires_individual_approval"])
        candidate = repository.stage_knowledge_candidates.call_args.args[2][0]
        self.assertEqual(len(candidate["content_hash"]), 64)
        self.assertEqual(candidate["client_request_id"], "candidate-request-1")

    def test_review_candidate_requires_boolean_approval(self):
        service = LearningSessionService(Mock())
        with self.assertRaisesRegex(ValueError, "boolean"):
            service.review_knowledge_candidate(
                "owner", "33333333-3333-4333-8333-333333333333", "yes",  # type: ignore[arg-type]
            )


class LearningReviewIntervalTests(unittest.TestCase):
    def test_mastery_expands_interval(self):
        self.assertEqual(next_review_interval(3, "mastered", "high"), 8)
        self.assertEqual(next_review_interval(3, "mastered", "medium"), 6)

    def test_partial_and_misconception_contract_interval(self):
        self.assertEqual(next_review_interval(10, "partial", "medium"), 3)
        self.assertEqual(next_review_interval(10, "misconception", "high"), 1)
        self.assertEqual(next_review_interval(10, "unknown", "low"), 1)


class LearningKnowledgeCommitTests(unittest.TestCase):
    @patch("src.api.agent_tool.commit_wiki_knowledge")
    @patch("src.api.agent_tool._learning_session_service")
    def test_approved_candidate_reuses_existing_knowledge_commit(self, service_factory, commit):
        repository = Mock()
        repository.claim_approved_knowledge_candidate.return_value = {
            "status": "committing", "title": "학습 결과", "description": "설명",
            "tags": ["oauth"], "content": "교정된 내용", "topic_name": None,
            "topic_update_text": None,
        }
        repository.mark_knowledge_candidate_committed.return_value = {
            "candidate_id": "33333333-3333-4333-8333-333333333333", "status": "committed",
        }
        service = Mock(repository=repository)
        service._uuid.return_value = "33333333-3333-4333-8333-333333333333"
        service_factory.return_value = service
        commit.return_value = json.dumps({
            "success": True,
            "data": {"qa_file_path": "qa/result.md", "topic_file_path": None},
        })
        token = current_user_config.set({"user_id": "owner", "api_key": "key"})
        try:
            raw = commit_wiki_learning_knowledge_candidate.__wrapped__.__wrapped__
            result = raw("33333333-3333-4333-8333-333333333333")
        finally:
            current_user_config.reset(token)

        self.assertEqual(result["candidate"]["status"], "committed")
        commit.assert_called_once_with(
            title="학습 결과", description="설명", tags=["oauth"],
            content="교정된 내용", topic_name=None, topic_update_text=None,
            visibility="private",
        )
        repository.mark_knowledge_candidate_committed.assert_called_once()

    @patch("src.api.agent_tool.commit_wiki_knowledge")
    @patch("src.api.agent_tool._learning_session_service")
    def test_failed_commit_releases_candidate_claim(self, service_factory, commit):
        repository = Mock()
        repository.claim_approved_knowledge_candidate.return_value = {
            "status": "committing", "title": "학습 결과", "description": "설명",
            "tags": [], "content": "내용", "topic_name": None, "topic_update_text": None,
        }
        service = Mock(repository=repository)
        service._uuid.return_value = "33333333-3333-4333-8333-333333333333"
        service_factory.return_value = service
        commit.return_value = json.dumps({"success": False, "message": "저장 실패"})
        token = current_user_config.set({"user_id": "owner", "api_key": "key"})
        try:
            raw = commit_wiki_learning_knowledge_candidate.__wrapped__.__wrapped__
            with self.assertRaisesRegex(Exception, "저장 실패"):
                raw("33333333-3333-4333-8333-333333333333")
        finally:
            current_user_config.reset(token)

        repository.release_knowledge_candidate_claim.assert_called_once()


if __name__ == "__main__":
    unittest.main()
