from typing import Any, Dict

from src.learning.domain.mastery import topic_mastery_state


class LearningDashboardService:
    def __init__(self, repository):
        self.repository = repository

    def get(self, owner_id: str, days: int = 30) -> Dict[str, Any]:
        if days not in {7, 30, 90}:
            raise ValueError("days는 7, 30, 90 중 하나여야 합니다.")
        result = self.repository.metrics(owner_id, days)
        sessions = result["sessions"]
        total_sessions = sessions.get("total_sessions", 0) or 0
        completed_sessions = sessions.get("completed_sessions", 0) or 0
        candidates = result["knowledge_candidates"]
        human_decisions = candidates["approved"] + candidates["committed"] + candidates["rejected"]
        human_approvals = candidates["approved"] + candidates["committed"]
        result["derived_metrics"] = {
            "session_completion_rate": round(completed_sessions / total_sessions, 4) if total_sessions else None,
            "human_candidate_approval_rate": round(human_approvals / human_decisions, 4) if human_decisions else None,
        }
        evidence_rates = {}
        for key, metrics in result.get("learning_evidence", {}).items():
            attempts = metrics.get("attempt_count", 0) or 0
            independent = metrics.get("independent_mastery_count", 0) or 0
            evidence_rates[key] = round(independent / attempts, 4) if attempts else None
        review_rates = {}
        for level, metrics in result.get("delayed_transfer_reviews", {}).items():
            attempts = metrics.get("attempt_count", 0) or 0
            independent = metrics.get("independent_mastery_count", 0) or 0
            review_rates[level] = round(independent / attempts, 4) if attempts else None
        result["derived_metrics"]["independent_mastery_rates"] = evidence_rates
        result["derived_metrics"]["delayed_transfer_retention_rates"] = review_rates
        calibration = result.get("metacognitive_calibration", {})
        calibrated_total = sum(
            calibration.get(key, 0) or 0
            for key in ("aligned", "overconfident", "underconfident")
        )
        result["derived_metrics"]["metacognitive_calibration_rate"] = (
            round((calibration.get("aligned", 0) or 0) / calibrated_total, 4)
            if calibrated_total else None
        )
        topic_inputs = result.pop("topic_mastery_inputs", {})
        misconceptions = result.get("recurring_misconceptions", {})
        for topic in result.get("topics", []):
            topic_name = topic.get("topic")
            topic["mastery"] = topic_mastery_state(topic_inputs.get(topic_name, {}))
            topic["recurring_misconceptions"] = misconceptions.get(topic_name, [])
        result["metric_contract"] = {
            "client_llm_assessments_are_ground_truth": False,
            "human_candidate_decisions_are_explicit": True,
            "review_attempts_are_observed_actions": True,
            "independent_mastery_requires_no_support": True,
            "calibration_excludes_insufficient_evidence": True,
            "notice": "LLM 판정은 흐름용 신호입니다. 독립 숙달률은 무힌트 mastered 기록이며, 외부 정답 라벨은 아닙니다.",
        }
        return result
