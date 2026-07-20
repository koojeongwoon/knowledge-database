VALID_CALIBRATION_SIGNALS = {"aligned", "overconfident", "underconfident", "insufficient_evidence"}


def calibration_signal(assessment: str, confidence: str, support_level: str) -> str:
    if assessment == "unverifiable" or support_level != "none":
        return "insufficient_evidence"
    independent_success = assessment == "mastered"
    if confidence == "high" and not independent_success:
        return "overconfident"
    if confidence == "low" and independent_success:
        return "underconfident"
    return "aligned"


def calibration_feedback(signal: str) -> dict:
    if signal not in VALID_CALIBRATION_SIGNALS:
        raise ValueError("올바르지 않은 메타인지 보정 신호입니다.")
    if signal == "overconfident":
        return {
            "calibration_action": "prediction_reflection",
            "calibration_prompt": "답변 전 확신의 근거와 실제로 놓친 단서를 비교해 보세요.",
        }
    if signal == "underconfident":
        return {
            "calibration_action": "evidence_based_confidence_update",
            "calibration_prompt": "힌트 없이 성공한 근거를 확인하고 다음 유사 문제의 예상 확신도를 조정해 보세요.",
        }
    if signal == "aligned":
        return {"calibration_action": "maintain", "calibration_prompt": None}
    return {"calibration_action": "collect_independent_evidence", "calibration_prompt": None}
