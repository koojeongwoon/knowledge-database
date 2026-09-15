# User-owned Broker embedding execution

The former Knowledge-specific consent and 30-day grant design has been removed. The registered key remains USER-owned within its IAM tenant and is usable through the owner’s services without another connection step.

MCP authentication supplies the local owner; the background retry queue supplies the persisted job owner. `BrokerIdentityRepository` maps that owner to its IAM subject using the authoritative user table, with no subject/tenant header or argument override. The projected workload token authenticates Knowledge/worker to Broker. Broker’s explicit issuer policy fixes the tenant and allowed actions and automatically selects that user’s active connection. Missing or ambiguous connections fail closed; shared-tenant keys are not substituted.

`/settings/embedding` and `/api/settings/embedding-binding` are removed. Migration 24 drops the obsolete binding metadata after consumer cutover. The prior 30-day grant has no role in execution. The per-request context freshness limit is automatically generated and requires no user renewal.

Embedding and retry context loads storage fields only. The opt-in `LLM_PROVIDER=broker` structured streaming adapter is implemented and tested. Production retains its current Codex OAuth model until a Codex Broker adapter is ready; the new LLM flag is not enabled. Broker is trusted to perform authenticated provider calls and return results; Knowledge still owns prompts, parsing, indexing and storage.

## Historical verification of the removed grant design

The following describes the previous deployment, not the current authorization contract.

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

### Production migration compatibility

Migration 24 deletes all legacy binding rows and retains the unused table. The shared PostgreSQL server preloads AGE without installing it in knowledge_db, causing DROP TABLE to fail on missing ag_catalog. No runtime route or repository reads the retained table.
