import os
from functools import lru_cache

import jwt
from jwt import PyJWKClient

KNOWLEDGE_TENANT_ID = os.getenv("KNOWLEDGE_TENANT_ID", "ten_9664c024babc4110")
AUTH_SERVER_URL = os.getenv(
    "AUTH_SERVER_URL", f"https://auth.snappytory.com/t/{KNOWLEDGE_TENANT_ID}"
).rstrip("/")
AUTH_TOKEN_ISSUER = os.getenv("AUTH_TOKEN_ISSUER", AUTH_SERVER_URL)
KNOWLEDGE_CLIENT_ID = os.getenv("KNOWLEDGE_CLIENT_ID", "knowledge-service")
MCP_DELEGATION_SCOPE = os.getenv("MCP_DELEGATION_SCOPE", "mcp")
MCP_DELEGATION_ACTOR_CLIENT_ID = os.getenv(
    "MCP_DELEGATION_ACTOR_CLIENT_ID", "cli_ab3d5bb39f894dff"
)


class KnowledgeClientMismatchError(jwt.InvalidTokenError):
    pass


class KnowledgeTenantMismatchError(jwt.InvalidTokenError):
    pass


class MissingTokenSubjectError(jwt.InvalidTokenError):
    pass


class MissingTokenEmailError(jwt.InvalidTokenError):
    pass


class GatewayActorMismatchError(jwt.InvalidTokenError):
    pass


class DelegationScopeMismatchError(jwt.InvalidTokenError):
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
        audience=KNOWLEDGE_CLIENT_ID,
    )
    if claims.get("client_id") != KNOWLEDGE_CLIENT_ID:
        raise KnowledgeClientMismatchError("Token was not issued for the knowledge service")
    if claims.get("tenant_id") != KNOWLEDGE_TENANT_ID:
        raise KnowledgeTenantMismatchError("Token was not issued for the knowledge tenant")
    if not claims.get("sub"):
        raise MissingTokenSubjectError("Token subject is missing")
    if not claims.get("email"):
        raise MissingTokenEmailError("Token standard OIDC email claim is missing")
    return claims


def verify_gateway_delegation_token(token: str) -> dict:
    """Verify a short-lived IAM token delegated by the configured Tools Gateway."""
    claims = verify_auth_token(token)
    actor = claims.get("act")
    if not isinstance(actor, dict) or actor.get("sub") != MCP_DELEGATION_ACTOR_CLIENT_ID:
        raise GatewayActorMismatchError("Token actor is not the configured Tools Gateway")
    scope = claims.get("scope")
    if not isinstance(scope, str) or MCP_DELEGATION_SCOPE not in scope.split():
        raise DelegationScopeMismatchError("Token does not grant MCP access")
    return claims
