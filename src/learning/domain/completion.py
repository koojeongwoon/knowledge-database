from typing import Any, Dict, List


REQUIRED_EVIDENCE = ("retrieval", "comprehension", "near_transfer", "far_transfer")


class LearningCompletionPolicy:
    @staticmethod
    def evaluate(evidence_rows: List[Dict[str, Any]]) -> Dict[str, Any]:
        evidence = {
            key: {"attempt_count": 0, "independent_mastery_count": 0, "verified": False}
            for key in REQUIRED_EVIDENCE
        }
        for row in evidence_rows:
            dimension = str(row.get("learning_dimension") or "retrieval")
            transfer_level = str(row.get("transfer_level") or "none")
            key = f"{transfer_level}_transfer" if dimension == "transfer" else dimension
            if key not in evidence:
                continue
            evidence[key]["attempt_count"] += int(row.get("attempt_count") or 0)
            evidence[key]["independent_mastery_count"] += int(row.get("independent_mastery_count") or 0)

        missing = []
        for key, value in evidence.items():
            value["verified"] = value["independent_mastery_count"] > 0
            if not value["verified"]:
                missing.append(key)

        next_target = missing[0] if missing else None
        return {
            "ready_to_complete": not missing,
            "evidence": evidence,
            "missing_evidence": missing,
            "next_question_contract": None if not next_target else {
                "target": next_target,
                "question_type": "application" if next_target.endswith("_transfer") else "retrieval",
                "learning_dimension": "transfer" if next_target.endswith("_transfer") else next_target,
                "transfer_level": next_target.removesuffix("_transfer") if next_target.endswith("_transfer") else "none",
                "support_level": "none",
                "rules": [
                    "기존 질문을 그대로 반복하지 않고 같은 핵심 원리를 요구하는 변형 문제를 만든다.",
                    "정답이나 근거를 먼저 노출하지 않는다.",
                    "힌트를 사용하면 independent_success로 판정하지 않는다.",
                ],
            },
        }
