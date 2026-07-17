from typing import Any, Dict


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
        result["metric_contract"] = {
            "client_llm_assessments_are_ground_truth": False,
            "human_candidate_decisions_are_explicit": True,
            "review_attempts_are_observed_actions": True,
            "notice": "LLM 판정은 튜터 흐름용 신호이며 실제 이해도의 정답 라벨이 아닙니다.",
        }
        return result
