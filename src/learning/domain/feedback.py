from typing import Any, Dict, List, Optional

from src.learning.domain.calibration import calibration_feedback, calibration_signal


VALID_ASSESSMENTS = {"mastered", "partial", "misconception", "unknown", "unverifiable"}
VALID_CONFIDENCE = {"low", "medium", "high"}
VALID_LEARNING_DIMENSIONS = {"retrieval", "comprehension", "transfer"}
VALID_TRANSFER_LEVELS = {"none", "near", "far"}
VALID_SUPPORT_LEVELS = {"none", "light", "substantial"}
MAX_LIST_ITEMS = 20
MAX_ITEM_LENGTH = 500


def _clean_list(values: Optional[List[str]]) -> List[str]:
    return [str(value).strip()[:MAX_ITEM_LENGTH] for value in (values or []) if str(value).strip()][:MAX_LIST_ITEMS]


class LearningFeedbackPlanner:
    def plan(
        self,
        assessment: str,
        confidence: str = "medium",
        missing_concepts: Optional[List[str]] = None,
        misconceptions: Optional[List[str]] = None,
        evidence_refs: Optional[List[str]] = None,
        hint: Optional[str] = None,
        next_question: Optional[str] = None,
        learning_dimension: str = "retrieval",
        transfer_level: str = "none",
        support_level: str = "none",
    ) -> Dict[str, Any]:
        normalized_assessment = (assessment or "").strip().lower()
        normalized_confidence = (confidence or "medium").strip().lower()
        dimension = (learning_dimension or "retrieval").strip().lower()
        transfer = (transfer_level or "none").strip().lower()
        support = (support_level or "none").strip().lower()
        if normalized_assessment not in VALID_ASSESSMENTS:
            raise ValueError("assessment는 mastered, partial, misconception, unknown, unverifiable 중 하나여야 합니다.")
        if normalized_confidence not in VALID_CONFIDENCE:
            raise ValueError("confidence는 low, medium, high 중 하나여야 합니다.")
        if dimension not in VALID_LEARNING_DIMENSIONS:
            raise ValueError("learning_dimension은 retrieval, comprehension, transfer 중 하나여야 합니다.")
        if transfer not in VALID_TRANSFER_LEVELS:
            raise ValueError("transfer_level은 none, near, far 중 하나여야 합니다.")
        if support not in VALID_SUPPORT_LEVELS:
            raise ValueError("support_level은 none, light, substantial 중 하나여야 합니다.")
        if dimension == "transfer" and transfer == "none":
            raise ValueError("transfer 판정에는 near 또는 far transfer_level이 필요합니다.")
        if dimension != "transfer" and transfer != "none":
            raise ValueError("transfer_level은 transfer 판정에서만 사용할 수 있습니다.")

        missing = _clean_list(missing_concepts)
        errors = _clean_list(misconceptions)
        refs = _clean_list(evidence_refs)
        safe_hint = (hint or "").strip()[:2000] or None
        safe_next_question = (next_question or "").strip()[:2000] or None

        if normalized_assessment == "partial" and not (missing or safe_hint):
            raise ValueError("partial 판정에는 missing_concepts 또는 hint가 필요합니다.")
        if normalized_assessment == "misconception" and not (errors or safe_hint):
            raise ValueError("misconception 판정에는 misconceptions 또는 hint가 필요합니다.")
        if normalized_assessment != "unverifiable" and not refs:
            raise ValueError("학습 판정에는 최소 하나의 evidence_refs가 필요합니다.")

        plan = self._plan_for(normalized_assessment, normalized_confidence)
        signal = calibration_signal(normalized_assessment, normalized_confidence, support)
        if normalized_assessment == "mastered" and support != "none":
            plan.update({
                "next_action": "fade_support_then_retry",
                "should_reask": True,
                "review_priority": "medium",
                "suggested_review_days": 3,
            })
        elif normalized_assessment == "mastered" and dimension == "transfer" and transfer == "far":
            plan["suggested_review_days"] = 14
        if signal == "overconfident":
            plan["review_priority"] = "critical"
        if not safe_hint and normalized_assessment in ("partial", "misconception", "unknown"):
            concepts = missing or errors
            safe_hint = (
                "다음 개념을 중심으로 기존 답을 다시 연결해 보세요: " + ", ".join(concepts)
                if concepts
                else "현재 알고 있는 부분과 모르는 부분을 나누어 다시 설명해 보세요."
            )

        return {
            "assessment_source": "client_llm",
            "server_llm_used": False,
            "assessment": normalized_assessment,
            "confidence": normalized_confidence,
            "missing_concepts": missing,
            "misconceptions": errors,
            "evidence_refs": refs,
            "hint": safe_hint,
            "next_question": safe_next_question,
            "learning_evidence": {
                "dimension": dimension,
                "transfer_level": transfer,
                "support_level": support,
                "independent_success": normalized_assessment == "mastered" and support == "none",
            },
            "metacognitive_calibration": {
                "signal": signal,
                **calibration_feedback(signal),
            },
            **plan,
        }

    @staticmethod
    def _plan_for(assessment: str, confidence: str) -> Dict[str, Any]:
        if assessment == "mastered":
            interval = 3 if confidence == "low" else 7
            return {
                "next_action": "advance",
                "should_reask": False,
                "feedback_sequence": ["brief_confirmation", "evidence_connection", "transfer_question"],
                "retry_prompt": None,
                "review_priority": "medium" if confidence == "low" else "low",
                "suggested_review_days": interval,
            }
        if assessment == "partial":
            return {
                "next_action": "hint_then_retry",
                "should_reask": True,
                "feedback_sequence": ["acknowledge_correct_parts", "one_hint", "retry_same_question"],
                "retry_prompt": "힌트를 바탕으로 누락된 부분을 보완해 같은 질문에 다시 답해 주세요.",
                "review_priority": "medium",
                "suggested_review_days": 3,
            }
        if assessment == "misconception":
            return {
                "next_action": "correct_then_retry",
                "should_reask": True,
                "feedback_sequence": ["identify_conflict", "one_hint", "self_correction", "retry_same_question"],
                "retry_prompt": "힌트를 바탕으로 기존 답변에서 수정할 부분을 찾아 다시 설명해 주세요.",
                "review_priority": "critical" if confidence == "high" else "high",
                "suggested_review_days": 1,
            }
        if assessment == "unknown":
            return {
                "next_action": "scaffold_then_retry",
                "should_reask": True,
                "feedback_sequence": ["activate_prior_knowledge", "one_hint", "partial_attempt"],
                "retry_prompt": "힌트를 바탕으로 아는 부분부터 단계적으로 답해 주세요.",
                "review_priority": "high",
                "suggested_review_days": 1,
            }
        return {
            "next_action": "request_better_evidence",
            "should_reask": False,
            "feedback_sequence": ["disclose_evidence_gap", "request_source_or_narrow_question"],
            "retry_prompt": None,
            "review_priority": "blocked",
            "suggested_review_days": None,
        }
