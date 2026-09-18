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


class ServiceAccessEnforcementError(jwt.InvalidTokenError):
    pass


class ServiceAccessDisabledError(ServiceAccessEnforcementError):
    pass


class ServiceAccessStaleError(ServiceAccessEnforcementError):
    pass


class ServiceAccessVersionMismatchError(ServiceAccessEnforcementError):
    pass


def is_service_access_enforcement_enabled() -> bool:
    return os.getenv("KNOWLEDGE_SERVICE_ACCESS_ENFORCEMENT_ENABLED", "false").lower() in ("true", "1", "yes")


def verify_service_access(tenant_id: str, subject_id: str, client_id: str, token_service_access_version: int, db_manager=None) -> None:
    """
    서비스 접근 인가 상태 및 토큰 버전을 로컬 DB 상태와 비교 검증합니다.
    KNOWLEDGE_SERVICE_ACCESS_ENFORCEMENT_ENABLED 가 True일 때 실행됩니다.
    """
    from src.core.database.factory import DatabaseManager
    manager = db_manager or DatabaseManager()
    with manager.cursor() as cur:
        # 1. 헬스체크 확인 (60초 내 heartbeat)
        cur.execute(
            """
            SELECT 1 FROM iam_user_service_access_health
            WHERE singleton AND last_seen_at > CURRENT_TIMESTAMP - INTERVAL '60 seconds'
            """
        )
        if not cur.fetchone():
            raise ServiceAccessStaleError("IAM user service access consumer is stale or offline")

        # 2. 로컬 서비스 접근 상태 확인
        cur.execute(
            """
            SELECT service_access_status, access_version
            FROM iam_user_service_access_states
            WHERE tenant_id = %s AND subject_id = %s AND client_id = %s
            """,
            (tenant_id, subject_id, client_id),
        )
        row = cur.fetchone()
        if row is not None:
            status, local_version = row
            if status != "ACTIVE":
                raise ServiceAccessDisabledError(f"User service access is not ACTIVE (status={status})")
            if token_service_access_version < local_version:
                raise ServiceAccessVersionMismatchError(
                    f"Token service_access_version ({token_service_access_version}) is older than local version ({local_version})"
                )


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


def verify_auth_token(token: str, db_manager=None) -> dict:
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

    if is_service_access_enforcement_enabled():
        raw_access_version = claims.get("service_access_version")
        if raw_access_version is None:
            raise ServiceAccessVersionMismatchError("Token is missing required service_access_version claim")
        try:
            service_access_version = int(raw_access_version)
        except (TypeError, ValueError) as exc:
            raise ServiceAccessVersionMismatchError("Invalid service_access_version claim") from exc

        verify_service_access(
            claims["tenant_id"],
            claims["sub"],
            claims["client_id"],
            service_access_version,
            db_manager=db_manager,
        )

    return claims


def verify_gateway_delegation_token(token: str, db_manager=None) -> dict:
    """Verify a short-lived IAM token delegated by the configured Tools Gateway."""
    if db_manager is not None:
        claims = verify_auth_token(token, db_manager=db_manager)
    else:
        claims = verify_auth_token(token)
    actor = claims.get("act")
    if not isinstance(actor, dict) or actor.get("sub") != MCP_DELEGATION_ACTOR_CLIENT_ID:
        raise GatewayActorMismatchError("Token actor is not the configured Tools Gateway")
    scope = claims.get("scope")
    if not isinstance(scope, str) or MCP_DELEGATION_SCOPE not in scope.split():
        raise DelegationScopeMismatchError("Token does not grant MCP access")
    return claims
