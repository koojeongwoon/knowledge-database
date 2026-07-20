VALID_REVIEW_PRIORITIES = {"low", "medium", "high", "critical", "blocked"}
VALID_DELAYED_TRANSFER_LEVELS = {"near", "far"}


def next_review_interval(previous_days: int, assessment: str, confidence: str) -> int:
    """복습 결과에 따라 1~365일 범위의 다음 간격을 결정합니다."""
    previous = max(1, min(int(previous_days), 365))
    if assessment == "mastered":
        multiplier = 2.5 if confidence == "high" else 2 if confidence == "medium" else 1.5
        return min(365, max(previous + 1, round(previous * multiplier)))
    if assessment == "partial":
        return min(3, previous)
    if assessment in {"misconception", "unknown"}:
        return 1
    return previous


def delayed_transfer_question_contract(review: dict) -> dict:
    transfer_level = str(review.get("transfer_level") or "near").lower()
    if transfer_level not in VALID_DELAYED_TRANSFER_LEVELS:
        raise ValueError("올바르지 않은 지연 전이 수준입니다.")
    return {
        "source_prompt": review.get("prompt"),
        "question_type": "application",
        "learning_dimension": "transfer",
        "transfer_level": transfer_level,
        "support_level": "none",
        "rules": [
            "source_prompt를 그대로 반복하지 않고 같은 핵심 원리를 요구하는 새 상황을 만든다.",
            "이전 답변, 정답, 근거 내용은 사용자가 답하기 전에 노출하지 않는다.",
            "near는 표면 조건을 바꾸고 far는 도메인이나 제약 조건을 바꾼다.",
            "힌트를 제공했다면 support_level을 기록하고 독립 성공으로 판정하지 않는다.",
        ],
    }


def next_delayed_transfer_level(current_level: str, assessment: str, independent_success: bool) -> str:
    current = str(current_level or "near").lower()
    if current not in VALID_DELAYED_TRANSFER_LEVELS:
        raise ValueError("올바르지 않은 지연 전이 수준입니다.")
    if current == "near" and assessment == "mastered" and independent_success:
        return "far"
    return current
