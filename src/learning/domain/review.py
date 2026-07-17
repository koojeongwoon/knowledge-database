VALID_REVIEW_PRIORITIES = {"low", "medium", "high", "critical", "blocked"}


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
