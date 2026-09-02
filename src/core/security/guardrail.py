"""
AI Prompt Injection & Safety Guardrail Module
- Defends against Indirect Prompt Injection in RAG & Knowledge ingestion.
- Functional validation and sanitization.
"""

import re
from typing import Tuple, List
from pydantic import BaseModel, ConfigDict, Field


class PromptSafetyResult(BaseModel):
    """불변 프롬프트 안전 검증 결과 Value Object (DDD/FP)"""
    model_config = ConfigDict(frozen=True)

    is_safe: bool = Field(description="안전 여부")
    risk_level: str = Field(default="NONE", description="위험도 (NONE, LOW, MEDIUM, HIGH)")
    detected_patterns: List[str] = Field(default_factory=list, description="탐지된 인젝션 패턴 목록")
    sanitized_text: str = Field(description="위험 요소가 정제된 텍스트")


# 간접 프롬프트 인젝션 의심 키워드 및 패턴 (System Prompt Override, Data Exfiltration)
PROMPT_INJECTION_PATTERNS = [
    (re.compile(r'(?i)\b(?:ignore|disregard|forget|bypass)\s+(?:all\s+)?(?:previous|prior|above)\s+(?:instructions|rules|directions|prompts)\b'), "PROMPT_OVERRIDE", "HIGH"),
    (re.compile(r'(?i)\b(?:system\s+prompt|initial\s+prompt|developer\s+mode|jailbreak)\b'), "SYSTEM_EXPOSURE", "MEDIUM"),
    (re.compile(r'(?i)\b(?:reveal|leak|output|dump|print)\s+(?:the\s+)?(?:secret|api[_-]?key|password|credentials|system\s+instructions)\b'), "DATA_EXFILTRATION", "HIGH"),
    (re.compile(r'(?i)<\s*(?:script|iframe|svg|img)[^>]*on\w+\s*='), "XSS_PAYLOAD", "HIGH"),
    (re.compile(r'\[\s*system\s*\]|\{\s*system\s*\}|<<\s*SYS\s*>>', re.IGNORECASE), "SYSTEM_DELIMITER_INJECTION", "HIGH"),
]


def validate_prompt_safety(text: str) -> PromptSafetyResult:
    """
    텍스트에 간접 프롬프트 인젝션 공격이 포함되어 있는지 검증하는 순수 함수.
    """
    if not text or not isinstance(text, str):
        return PromptSafetyResult(is_safe=True, risk_level="NONE", detected_patterns=[], sanitized_text=text or "")

    detected = []
    max_risk = "NONE"
    sanitized = text

    for pattern, name, risk in PROMPT_INJECTION_PATTERNS:
        if pattern.search(text):
            detected.append(name)
            if risk == "HIGH" or (risk == "MEDIUM" and max_risk != "HIGH"):
                max_risk = risk
            # 정제: 위험 구문 무해화
            sanitized = pattern.sub(f"[FILTERED_{name}]", sanitized)

    is_safe = len(detected) == 0
    return PromptSafetyResult(
        is_safe=is_safe,
        risk_level=max_risk if detected else "NONE",
        detected_patterns=detected,
        sanitized_text=sanitized,
    )
