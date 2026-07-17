import unittest
from unittest.mock import Mock

from src.learning.application.dashboard import LearningDashboardService


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
            "source_counts": {"inbox": 2, "knowledge": 3},
            "topics": [], "recent_sessions": [], "period_days": 30,
        }

        result = LearningDashboardService(repository).get("owner", 30)

        self.assertEqual(result["derived_metrics"]["session_completion_rate"], 0.75)
        self.assertEqual(result["derived_metrics"]["human_candidate_approval_rate"], 0.75)
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
            "recent_sessions": [], "period_days": 7,
        }
        result = LearningDashboardService(repository).get("owner", 7)
        self.assertIsNone(result["derived_metrics"]["session_completion_rate"])
        self.assertIsNone(result["derived_metrics"]["human_candidate_approval_rate"])


if __name__ == "__main__":
    unittest.main()
