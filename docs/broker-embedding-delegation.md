# Broker embedding delegation

`/settings/embedding` authorizes the current IAM user’s Broker connection for Knowledge search and automatic indexing for 1, 7, or 30 days. Knowledge stores only connection/version/grant metadata in `knowledge_embedding_bindings` (migration 23). No external API key is entered or retrieved for embedding.

Set `EMBEDDING_PROVIDER=broker` only after active owners have authorized their bindings. Unconfigured, expired, revoked, or rotated bindings fail closed. Both the MCP factory and indexing factory capture the verified local owner and use `/v1/delegated-execute` with the workload’s projected Broker-audience token. Each batch rereads binding metadata and the token; no IAM or external provider key enters the embedding request.

The consent page requires an IAM session and same-origin writes. Concurrent binding changes return 409. Rebinding revokes old permission first; a failed replacement requires retry. Use “Knowledge 사용 허용 해제” to revoke the grant without revoking the original Broker connection.

Search and indexing embedding configuration loads storage fields only. Optional document expansion still uses the legacy LLM settings path; migrating those credentials is separate work.

Tests: `python -m pytest -q tests/test_embedding_delegation.py tests/test_broker_embedding.py tests/test_architecture_boundaries.py tests/test_database_migrations.py tests/test_llm_auth_settings.py tests/test_document_expansion.py tests/test_settings_web.py tests/test_scoped_indexing.py tests/test_file_indexing_executor.py tests/test_mcp_tool_contract.py`.
