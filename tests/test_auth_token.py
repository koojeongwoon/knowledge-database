from unittest.mock import Mock, patch

import pytest

from src.api_keys import auth


def _valid_claims(**overrides):
    return {
        "sub": "auth-user-1",
        "email": "user@example.com",
        "client_id": auth.KNOWLEDGE_CLIENT_ID,
        "tenant_id": auth.KNOWLEDGE_TENANT_ID,
        **overrides,
    }


def test_tenant_defaults_use_the_knowledge_tenant_issuer():
    tenant_base = "https://auth.snappytory.com/t/ten_9664c024babc4110"
    assert auth.KNOWLEDGE_TENANT_ID == "ten_9664c024babc4110"
    assert auth.AUTH_SERVER_URL == tenant_base
    assert auth.AUTH_TOKEN_ISSUER == tenant_base


def test_verification_requires_tenant_issuer_and_client_audience(monkeypatch):
    jwk_client = Mock()
    jwk_client.get_signing_key_from_jwt.return_value = Mock(key="public-key")
    monkeypatch.setattr(auth, "_jwk_client", lambda: jwk_client)

    with patch.object(auth.jwt, "decode", return_value=_valid_claims()) as decode:
        assert auth.verify_auth_token("signed-token")["sub"] == "auth-user-1"

    decode.assert_called_once_with(
        "signed-token",
        "public-key",
        algorithms=["RS256"],
        issuer=auth.AUTH_TOKEN_ISSUER,
        audience=auth.KNOWLEDGE_CLIENT_ID,
    )


@pytest.mark.parametrize(
    ("claims", "error"),
    [
        (_valid_claims(client_id="another-client"), auth.KnowledgeClientMismatchError),
        (_valid_claims(tenant_id="another-tenant"), auth.KnowledgeTenantMismatchError),
        (_valid_claims(sub=""), auth.MissingTokenSubjectError),
        (_valid_claims(email=""), auth.MissingTokenEmailError),
    ],
)
def test_verification_rejects_wrong_service_or_identity_claims(monkeypatch, claims, error):
    jwk_client = Mock()
    jwk_client.get_signing_key_from_jwt.return_value = Mock(key="public-key")
    monkeypatch.setattr(auth, "_jwk_client", lambda: jwk_client)

    with patch.object(auth.jwt, "decode", return_value=claims):
        with pytest.raises(error):
            auth.verify_auth_token("signed-token")
