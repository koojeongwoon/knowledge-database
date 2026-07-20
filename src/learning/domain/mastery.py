from typing import Any, Dict


MASTERY_STAGES = ("unassessed", "acquiring", "transfer_ready", "retained")


def topic_mastery_state(evidence: Dict[str, Any]) -> Dict[str, Any]:
    verified = {
        key: int(evidence.get(key) or 0) > 0
        for key in ("retrieval", "comprehension", "near_transfer", "far_transfer")
    }
    retained = int(evidence.get("far_review") or 0) > 0
    if retained and all(verified.values()):
        stage = "retained"
    elif all(verified.values()):
        stage = "transfer_ready"
    elif any(verified.values()):
        stage = "acquiring"
    else:
        stage = "unassessed"
    return {
        "stage": stage,
        "verified_dimensions": [key for key, value in verified.items() if value],
        "missing_dimensions": [key for key, value in verified.items() if not value],
        "far_transfer_retained": retained,
    }
