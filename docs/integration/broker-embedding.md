# Credential Broker embedding slice

The new `/api/settings/embeddings` POST route uses the existing Knowledge IAM login
session. It exchanges the server-held IAM access token for the Broker client, reads
the projected Kubernetes workload token, and calls Broker `/v1/execute`. The route
never calls `UserSettingsService` or IAM's AI credential bundle and returns only vectors.

Required non-secret runtime settings:

- `IAM_SERVER_URL`: IAM root URL (the exchange path is `/api/auth/oauth2/token`).
- `IAM_TENANT_ID`: defaults to shared tenant `ten_9664c024babc4110`.
- `CREDENTIAL_BROKER_URL`: internal Credential Broker origin.
- `BROKER_WORKLOAD_TOKEN_FILE`: defaults to `/var/run/secrets/credential-broker/token`.

Request body: `connection_id` (UUID), `credential_version` (positive integer),
`input` (1–100 text strings), and optional `dimensions` (1–1536, default 1536).
The enabled model is `text-embedding-3-small`. The IAM session must be authorized
for the Broker service. Broker enforces actual connection ownership and version;
Knowledge never derives ownership from the supplied connection ID.

`BrokerEmbeddingService` implements the existing embedding service interface and
is wired for the opt-in `EMBEDDING_PROVIDER=broker` factory setting. Factory callers
also require `BROKER_EMBEDDING_CONNECTION_ID`, `BROKER_EMBEDDING_CREDENTIAL_VERSION`,
and the separate request-local `broker_subject_token` context. Do not set this
provider globally until MCP and indexing jobs carry a verified IAM delegation.
A Knowledge API key, database owner ID, or provider key is not a substitute.
Missing IAM proof fails closed without a direct OpenAI fallback.

Provider calls are not retried automatically. Knowledge issues one request UUID per
batch. Broker persists admission and permits a successful replay with the same
request UUID; uncertain/failed duplicate requests are blocked. A new Knowledge call
is a new logical request and can incur another charge after an uncertain outcome.

Validation: 66 focused tests passed, including Broker client/session and denial-status
regression tests. Production commit `3974214` was deployed via CI run `34938354222`.
On 2026-09-15, the real Knowledge IAM session -> Broker -> OpenAI path returned
HTTP 200 with one finite 1536-dimensional vector. The previously revoked test
connection returned JSON HTTP 409, with a Broker DENIED audit and no execution
admission. The newly registered connection remains ACTIVE at version 1.

Final successful request: `41170f5b-976a-49ce-b48e-037d2bf9e1c6` (6 tokens).
Final denied request: `2d22f886-e485-4e90-8757-be0960ef109d`.
Runtime image digest: `sha256:daa3130d8275147aebb7e3668c2bdaba79b639e786a9f664dce66b7232f42eac`.
The image matched CI and Argo CD reported Synced/Healthy.

Existing MCP/worker embedding paths and legacy LLM credential caches remain on
their prior settings; this verifies the explicit IAM-session route, not a global
consumer cutover.
