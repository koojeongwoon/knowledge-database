# Knowledge authenticated provider transport

Knowledge owns provider HTTP requests and response interpretation. Broker verifies the personal connection and injects authentication before forwarding raw bytes. The common contract is documented in Broker `docs/implementation/authenticated-http-contract.md`.

- OpenAI embeddings: Knowledge constructs `/v1/embeddings` JSON and validates vector indexes, count, dimensions and finite values.
- OpenAI API-key chat: Knowledge constructs Chat Completions JSON/strict schema and parses its SSE stream.
- Codex OAuth: Knowledge constructs Codex Responses input/instructions/strict schema and parses Responses SSE. The user's `gpt-5.6-luna` preference is retained; unsupported temperature/output-token fields are omitted.
- Broker endpoints are `/v1/proxy/api-key` and `/v1/proxy/oauth`, with raw provider body and base64 JSON `X-Broker-Request` metadata. No provider key/token is returned to Knowledge.

## Identity and runtime

`BrokerIdentityRepository` maps the authenticated local owner to persisted IAM `sub_val`. Browser routes authenticate the existing Knowledge session; MCP uses its authenticated owner; workers use the persisted job owner. Missing identity fails closed. Client-provided subject/tenant overrides and shared-tenant credential fallback are not accepted.

Required non-secret settings:

- `CREDENTIAL_BROKER_URL`: internal Broker origin.
- `BROKER_WORKLOAD_TOKEN_FILE`: projected, rotating Broker-audience Kubernetes token.
- `EMBEDDING_PROVIDER=broker` and `LLM_PROVIDER=broker`.

Broker must first enable server profiles `openai` and `codex` and explicit `http.forward` issuer policy for Knowledge and its indexing worker. No per-service connection approval or 30-day grant is needed. Historical action labels on the personal connection are not business-operation filters on this new transport; service business authorization remains required.

`POST /api/settings/embeddings` retains `connection_id`, positive `credential_version`, `input` and optional `dimensions` (1–1536). The existing session supplies the owner; Broker checks connection ownership and version. Factory callers use the current owner context and automatic unique personal connection selection; no extra connection environment settings or user token exchange are needed for execution.

`BrokerHttpClient` reads the workload token on each attempt, uses fresh request UUIDs, and distinguishes Broker errors from provider HTTP errors by Broker's response-source marker. Embeddings are never automatically retried. Codex generation opts into one repeat only after a provider 401 and successful Broker authentication refresh. Uncertain failures and other provider errors are not retried. Truncated streams are rejected by Knowledge's provider parser.

OAuth link/refresh/unlink and credential custody currently remain in IAM behind the Broker-only personal adapter. Removing all legacy IAM raw credential APIs is a subsequent migration, not claimed by this consumer cutover.

## Verification

Local focused suite: 63 passing tests covering raw provider requests, preserved OAuth/model, strict response parsing, user mapping, workload rotation, bounded opt-in authentication retry, no credential-cache reads, browser denial status and document indexing/expansion consumers. Production evidence is recorded in Broker's authenticated proxy implementation plan after rollout.
