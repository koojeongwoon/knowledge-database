"""
PII Sanitizer Module (Data Protection & Masking)
- DDD Value Object & Pure FP Functions
- Masks Resident Registration Numbers (RRN), Credit Cards, API Keys, Passwords, Bearer Tokens.
"""

import re
from typing import Any, Dict, List, Union


# Regex Patterns
# 1. 한국 주민등록번호 / 외국인등록번호 패턴 (e.g. 900101-1234567, 9001011234567)
RRN_PATTERN = re.compile(r'\b\d{2}(?:0[1-9]|1[0-2])(?:0[1-9]|[12]\d|3[01])[- ]?[1-8]\d{6}\b')

# 2. 신용카드 번호 (16자리, 4-4-4-4 또는 16연속 숫자)
CREDIT_CARD_PATTERN = re.compile(r'\b(?:\d{4}[- ]?){3}\d{4}\b')

# 3. OpenAI / 일반 API Key 패턴 (sk-..., ak-..., Bearer ...)
API_KEY_PATTERNS = [
    re.compile(r'\b(sk-[a-zA-Z0-9T3BlbkFJ]{20,})\b'),
    re.compile(r'\b(AKIA[0-9A-Z]{16})\b'),  # AWS Access Key
    re.compile(r'Bearer\s+([a-zA-Z0-9\-._~+/]+=*)', re.IGNORECASE),
]

# 4. 키-값 기반 비밀번호/시크릿 마스킹 정규식
SENSITIVE_KEY_PATTERN = re.compile(
    r'(password|passwd|secret|api_key|access_key|token|auth_token|secret_key)\s*[:=]\s*([\'"][^\'"]+[\'"]|[^\s,;]+)',
    re.IGNORECASE
)


def sanitize_text(text: str) -> str:
    """
    텍스트 내의 민감 정보를 식별하여 [REDACTED] 처리하는 순수 함수.
    """
    if not isinstance(text, str) or not text:
        return text

    sanitized = text

    # 1. 주민등록번호 마스킹
    sanitized = RRN_PATTERN.sub("[REDACTED]", sanitized)

    # 2. 카드번호 마스킹
    sanitized = CREDIT_CARD_PATTERN.sub("[REDACTED]", sanitized)

    # 3. API Keys 마스킹
    for pat in API_KEY_PATTERNS:
        sanitized = pat.sub("[REDACTED]", sanitized)

    # 4. 비밀번호 및 키 값 쌍 마스킹
    def _mask_secret(match: re.Match) -> str:
        key_name = match.group(1)
        sep = "=" if "=" in match.group(0) else ":"
        return f"{key_name}{sep}[REDACTED]"

    sanitized = SENSITIVE_KEY_PATTERN.sub(_mask_secret, sanitized)

    return sanitized


def sanitize_dict(data: Union[Dict[str, Any], List[Any], Any]) -> Any:
    """
    딕셔너리, 리스트 등 중첩 구조 데이터를 재귀적으로 불변 복사하며 마스킹하는 순수 함수.
    """
    if isinstance(data, dict):
        sanitized_dict = {}
        for k, v in data.items():
            lower_k = str(k).lower()
            if any(s in lower_k for s in ["password", "secret", "token", "api_key"]):
                if isinstance(v, (str, int, float)):
                    sanitized_dict[k] = "[REDACTED]"
                else:
                    sanitized_dict[k] = sanitize_dict(v)
            else:
                sanitized_dict[k] = sanitize_dict(v)
        return sanitized_dict
    elif isinstance(data, list):
        return [sanitize_dict(item) for item in data]
    elif isinstance(data, tuple):
        return tuple(sanitize_dict(item) for item in data)
    elif isinstance(data, str):
        return sanitize_text(data)
    else:
        return data
