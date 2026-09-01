import pytest
from unittest.mock import patch, MagicMock
from src.settings.iam_codex_client import IAMCodexClient


def test_iam_codex_client_get_token_success():
    client = IAMCodexClient(iam_base_url="http://mock-iam:8080")

    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "access_token": "iam-managed-access-token",
        "token_type": "Bearer",
        "expires_in": 3600,
        "source": "USER",
    }

    with patch("httpx.Client.get", return_value=mock_response):
        result = client.get_valid_token(user_id="user-123", org_id="org-456")
        assert result is not None
        assert result["access_token"] == "iam-managed-access-token"
        assert result["source"] == "USER"


def test_iam_codex_client_get_ai_bundle_success():
    client = IAMCodexClient(iam_base_url="http://mock-iam:8080")

    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "user_id": "user-123",
        "org_id": "org-456",
        "codex": {"linked": True, "access_token": "codex-token-123"},
        "openai_api_key": {"configured": True, "api_key": "sk-proj-openai-key"},
        "embedding_api_key": {"configured": True, "api_key": "sk-proj-embed-key"},
    }

    with patch("httpx.Client.get", return_value=mock_response):
        result = client.get_ai_bundle(user_id="user-123", org_id="org-456")
        assert result is not None
        assert result["codex"]["access_token"] == "codex-token-123"
        assert result["openai_api_key"]["api_key"] == "sk-proj-openai-key"
        assert result["embedding_api_key"]["api_key"] == "sk-proj-embed-key"
