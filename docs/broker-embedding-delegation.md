# Broker embedding delegation

`/settings/embedding` authorizes the current IAM user’s Broker connection for Knowledge search and automatic indexing for 1, 7, or 30 days. Knowledge stores only connection/version/grant metadata in `knowledge_embedding_bindings` (migration 23). No external API key is entered or retrieved for embedding.

Set `EMBEDDING_PROVIDER=broker` only after active owners have authorized their bindings. Unconfigured, expired, revoked, or rotated bindings fail closed. Both the MCP factory and indexing factory capture the verified local owner and use `/v1/delegated-execute` with the workload’s projected Broker-audience token. Each batch rereads binding metadata and the token; no IAM or external provider key enters the embedding request.

The consent page requires an IAM session and same-origin writes. Concurrent binding changes return 409. Rebinding revokes old permission first; a failed replacement requires retry. Use “Knowledge 사용 허용 해제” to revoke the grant without revoking the original Broker connection.

Search and indexing embedding configuration loads storage fields only. Optional document expansion still uses the legacy LLM settings path; migrating those credentials is separate work.

Tests: `python -m pytest -q tests/test_embedding_delegation.py tests/test_broker_embedding.py tests/test_architecture_boundaries.py tests/test_database_migrations.py tests/test_llm_auth_settings.py tests/test_document_expansion.py tests/test_settings_web.py tests/test_scoped_indexing.py tests/test_file_indexing_executor.py tests/test_mcp_tool_contract.py`.

## Live verification — 2026-09-15

- Source: Broker `6fb2c41`; Knowledge `18b3117` plus `0b7e510` (trusted public-origin CSRF validation and retry-worker storage-only config).
- GitOps: `523deb0`, `d5b0b1d`, final provider cutover `13d6a70`.
- Broker migration `20260915_05` in `broker`; Knowledge migration 23 in its existing schema. Original user connection remains ACTIVE:v1.
- User explicitly approved a 30-day Knowledge + indexing-worker grant. Binding `440ad960-d7ad-481e-8e0d-4298bee7beef` expires 2026-10-15 07:07:06 UTC. All active Knowledge API-key owners were covered (1/1). Other unconfigured owners fail closed.
- `EMBEDDING_PROVIDER=broker` is active in the running Knowledge Pod and shared worker ConfigMap.
- Real CronJob processed a new temporary private Markdown document through storage -> durable job queue -> parser -> Broker -> OpenAI -> PostgreSQL. One 1536-dimensional vector was stored; queue item completed. Worker Broker request `65f0a91d-c675-42f8-a4bb-3bcd0045c4dc` succeeded with 30 tokens and grant provenance.
- A fresh MCP SDK session from the Gateway Pod used its existing Knowledge auth configuration, listed the tool, and searched successfully, finding that document. Knowledge Broker request `c65fcb23-e0f0-4c9c-888d-18a0db320bd8` succeeded with 3 tokens. Codex's pre-existing connector session separately returned `Session not found`; no claim that this client session was repaired.
- Wrong connection-version request `a36fc958-b037-4a49-ab15-a643eb1e707d` returned 409, DELEGATION_DENIED, no vectors, and zero durable execution admissions.
- Temporary source document, indexed rows, ontology shadow, and queue entry were removed after verification. Audit evidence remains. User connection and grant remain active.
- New Pod digests match CI: Broker `sha256:fd32236350362d5a25b9694b9eec77cfb7c10d9b08ba649d137e5b6677805d55`; Knowledge `sha256:ec950f781c5a9feda0cbd7cf599a7cfa1f8be31926a53bfaf991336e00d3bca3`. Deployments ready; Argo applications Synced/Healthy.
- Broker tests: 126 passed, 6 opt-in DB tests skipped; separately migration scenario passed and five DB integration tests passed. Knowledge focused suite 84 passed; final origin/retry changes passed a 38-test subset including nine delegation tests. A broad Knowledge run had 289 passed, 6 skipped, and one asynchronous audit-file timing failure while its DB handler was blocked on an unavailable local DB; that unchanged guardrail file passed all 3 tests separately. This is not a claim of a clean full-suite run.
- Recent Broker/Knowledge logs contained zero provider-key and Transit-ciphertext pattern matches. Embedding does not load/cache provider credentials; optional document-expansion LLM and other legacy AI bundle consumers remain Phase 4 scope.
