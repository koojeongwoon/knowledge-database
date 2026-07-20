import unittest
from unittest.mock import Mock

from src.learning.application.dashboard import LearningDashboardService
from src.learning.domain.mastery import topic_mastery_state


class LearningDashboardServiceTests(unittest.TestCase):
    def test_metrics_keep_llm_labels_separate_from_human_decisions(self):
        repository = Mock()
        repository.metrics.return_value = {
            "sessions": {"active_sessions": 1, "completed_sessions": 3, "total_sessions": 4},
            "reviews": {"due_reviews": 2, "scheduled_reviews": 5, "review_attempts_period": 7},
            "knowledge_candidates": {
                "pending": 1, "approved": 1, "rejected": 1, "committing": 0, "committed": 2,
            },
            "client_llm_assessments": {"mastered": 4, "partial": 2, "misconception": 1, "unknown": 0, "unverifiable": 0},
            "learning_evidence": {
                "retrieval": {"attempt_count": 4, "independent_mastery_count": 3},
                "comprehension": {"attempt_count": 2, "independent_mastery_count": 1},
                "near_transfer": {"attempt_count": 2, "independent_mastery_count": 1},
                "far_transfer": {"attempt_count": 1, "independent_mastery_count": 0},
            },
            "delayed_transfer_reviews": {
                "near": {"attempt_count": 4, "independent_mastery_count": 3, "scheduled_count": 2, "due_count": 1},
                "far": {"attempt_count": 0, "independent_mastery_count": 0, "scheduled_count": 1, "due_count": 0},
            },
            "metacognitive_calibration": {
                "aligned": 6, "overconfident": 2, "underconfident": 2, "insufficient_evidence": 1,
            },
            "source_counts": {"inbox": 2, "knowledge": 3},
            "topics": [{"topic": "OAuth", "session_count": 2, "attempt_count": 8, "misconception_labels": 3}],
            "topic_mastery_inputs": {
                "OAuth": {"retrieval": 1, "comprehension": 1, "near_transfer": 1, "far_transfer": 1, "far_review": 1},
            },
            "recurring_misconceptions": {
                "OAuth": [{"misconception": "PKCE가 secret을 대체한다", "occurrence_count": 2}],
            },
            "recent_sessions": [], "period_days": 30,
        }

        result = LearningDashboardService(repository).get("owner", 30)

        self.assertEqual(result["derived_metrics"]["session_completion_rate"], 0.75)
        self.assertEqual(result["derived_metrics"]["human_candidate_approval_rate"], 0.75)
        self.assertEqual(result["derived_metrics"]["independent_mastery_rates"]["retrieval"], 0.75)
        self.assertEqual(result["derived_metrics"]["independent_mastery_rates"]["far_transfer"], 0.0)
        self.assertEqual(result["derived_metrics"]["delayed_transfer_retention_rates"]["near"], 0.75)
        self.assertIsNone(result["derived_metrics"]["delayed_transfer_retention_rates"]["far"])
        self.assertEqual(result["derived_metrics"]["metacognitive_calibration_rate"], 0.6)
        self.assertEqual(result["topics"][0]["mastery"]["stage"], "retained")
        self.assertEqual(result["topics"][0]["recurring_misconceptions"][0]["occurrence_count"], 2)
        self.assertNotIn("topic_mastery_inputs", result)
        self.assertFalse(result["metric_contract"]["client_llm_assessments_are_ground_truth"])
        self.assertTrue(result["metric_contract"]["human_candidate_decisions_are_explicit"])
        repository.metrics.assert_called_once_with("owner", 30)

    def test_period_is_restricted_to_dashboard_options(self):
        with self.assertRaisesRegex(ValueError, "7, 30, 90"):
            LearningDashboardService(Mock()).get("owner", 14)

    def test_empty_metrics_do_not_invent_rates(self):
        repository = Mock()
        repository.metrics.return_value = {
            "sessions": {"active_sessions": 0, "completed_sessions": 0, "total_sessions": 0},
            "reviews": {},
            "knowledge_candidates": {
                "pending": 0, "approved": 0, "rejected": 0, "committing": 0, "committed": 0,
            },
            "client_llm_assessments": {}, "source_counts": {}, "topics": [],
            "learning_evidence": {}, "delayed_transfer_reviews": {},
            "metacognitive_calibration": {},
            "topic_mastery_inputs": {}, "recurring_misconceptions": {},
            "recent_sessions": [], "period_days": 7,
        }
        result = LearningDashboardService(repository).get("owner", 7)
        self.assertIsNone(result["derived_metrics"]["session_completion_rate"])
        self.assertIsNone(result["derived_metrics"]["human_candidate_approval_rate"])
        self.assertEqual(result["derived_metrics"]["independent_mastery_rates"], {})
        self.assertIsNone(result["derived_metrics"]["metacognitive_calibration_rate"])


class TopicMasteryStateTests(unittest.TestCase):
    def test_stage_requires_independent_far_review_for_retention(self):
        transfer_ready = topic_mastery_state({
            "retrieval": 1, "comprehension": 1, "near_transfer": 1, "far_transfer": 1,
            "far_review": 0,
        })
        retained = topic_mastery_state({
            "retrieval": 1, "comprehension": 1, "near_transfer": 1, "far_transfer": 1,
            "far_review": 1,
        })

        self.assertEqual(transfer_ready["stage"], "transfer_ready")
        self.assertEqual(retained["stage"], "retained")

    def test_partial_evidence_is_acquiring_and_reports_gaps(self):
        result = topic_mastery_state({"retrieval": 2})

        self.assertEqual(result["stage"], "acquiring")
        self.assertIn("far_transfer", result["missing_dimensions"])


if __name__ == "__main__":
    unittest.main()
