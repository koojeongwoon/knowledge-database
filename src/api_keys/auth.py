import os
from functools import lru_cache

import jwt
from jwt import PyJWKClient

AUTH_SERVER_URL = os.getenv("AUTH_SERVER_URL", "https://auth.snappytory.com").rstrip("/")
AUTH_TOKEN_ISSUER = os.getenv("AUTH_TOKEN_ISSUER", "msa-auth-service")
KNOWLEDGE_CLIENT_ID = os.getenv("KNOWLEDGE_CLIENT_ID", "knowledge-service")
KNOWLEDGE_TENANT_ID = os.getenv("KNOWLEDGE_TENANT_ID", "knowledge")


class KnowledgeClientMismatchError(jwt.InvalidTokenError):
    pass


class KnowledgeTenantMismatchError(jwt.InvalidTokenError):
    pass


class MissingTokenSubjectError(jwt.InvalidTokenError):
    pass


@lru_cache(maxsize=1)
def _jwk_client() -> PyJWKClient:
    return PyJWKClient(
        f"{AUTH_SERVER_URL}/oauth2/jwks",
        headers={
            "User-Agent": "llm-wiki-jwks/1.0",
            "Accept": "application/json",
        },
        timeout=10,
    )


def verify_auth_token(token: str) -> dict:
    """인증서버가 발급한 지식베이스용 로그인 JWT를 검증합니다."""
    signing_key = _jwk_client().get_signing_key_from_jwt(token)
    claims = jwt.decode(
        token,
        signing_key.key,
        algorithms=["RS256"],
        issuer=AUTH_TOKEN_ISSUER,
        options={"verify_aud": False},
    )
    if claims.get("client_id") != KNOWLEDGE_CLIENT_ID:
        raise KnowledgeClientMismatchError("Token was not issued for the knowledge service")
    if claims.get("tenant_id") != KNOWLEDGE_TENANT_ID:
        raise KnowledgeTenantMismatchError("Token does not belong to the knowledge tenant")
    if not claims.get("sub"):
        raise MissingTokenSubjectError("Token subject is missing")
    return claims
