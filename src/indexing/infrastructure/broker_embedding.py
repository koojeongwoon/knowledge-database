"""Knowledge embedding adapter: IAM token exchange -> Broker -> vectors only."""
import contextvars
import math
import os
from pathlib import Path
from uuid import uuid4

import httpx

from src.indexing.domain.embedding import BaseEmbeddingService

# Separate request-local authentication proof. Never put this into settings caches.
broker_subject_token = contextvars.ContextVar("broker_subject_token", default=None)


class BrokerEmbeddingError(RuntimeError):
    pass


class BrokerEmbeddingService(BaseEmbeddingService):
    def __init__(self, dimension=1536, connection_id=None, credential_version=None,
                 subject_token_supplier=None, transport=None):
        self.dimension = dimension
        self.connection_id = connection_id or os.getenv("BROKER_EMBEDDING_CONNECTION_ID", "")
        self.credential_version = credential_version or int(os.getenv("BROKER_EMBEDDING_CREDENTIAL_VERSION", "1"))
        self.subject_token_supplier = subject_token_supplier or broker_subject_token.get
        self.transport = transport

    def get_dimension(self):
        return self.dimension

    def embed_text(self, text):
        return self.embed_batch([text])[0]

    def embed_batch(self, texts, batch_size=100):
        if not 1 <= batch_size <= 100:
            raise ValueError("Broker embedding batch size must be between 1 and 100")
        if not texts:
            return []
        if not self.connection_id:
            raise BrokerEmbeddingError("Broker embedding connection is not configured")
        output = []
        for offset in range(0, len(texts), batch_size):
            output.extend(self._execute(texts[offset:offset + batch_size]))
        return output

    def _execute(self, texts):
        subject_token = self.subject_token_supplier()
        if not subject_token:
            raise BrokerEmbeddingError("An IAM user token is required for Broker embeddings")
        iam_url = os.getenv("IAM_SERVER_URL", "").rstrip("/")
        endpoint = f"{iam_url}/api/auth/oauth2/token"
        tenant_id = os.getenv("IAM_TENANT_ID", "ten_9664c024babc4110")
        base_url = os.getenv("CREDENTIAL_BROKER_URL", "").rstrip("/")
        if not iam_url or not base_url:
            raise BrokerEmbeddingError("Broker authentication is not configured")
        try:
            with httpx.Client(timeout=40, follow_redirects=False, transport=self.transport) as client:
                exchanged = client.post(endpoint, data={
                    "grant_type": "urn:ietf:params:oauth:grant-type:token-exchange",
                    "subject_token_type": "urn:ietf:params:oauth:token-type:access_token",
                    "subject_token": subject_token,
                    "client_id": "credential-broker",
                    "requested_target_tenant": tenant_id,
                })
                if exchanged.status_code != 200:
                    raise BrokerEmbeddingError("Broker token exchange was rejected")
                iam_token = exchanged.json()["access_token"]
                # Read on each call so projected token rotation takes effect immediately.
                workload_token = Path(os.getenv(
                    "BROKER_WORKLOAD_TOKEN_FILE", "/var/run/secrets/credential-broker/token",
                )).read_text().strip()
                if not workload_token or not isinstance(iam_token, str) or not iam_token:
                    raise BrokerEmbeddingError("Broker authentication token is missing")
                response = client.post(f"{base_url}/v1/execute", headers={
                    "Authorization": f"Bearer {iam_token}",
                    "X-Workload-Authorization": f"Bearer {workload_token}",
                }, json={
                    "request_id": str(uuid4()), "connection_id": self.connection_id,
                    "credential_version": self.credential_version, "action": "embedding.create",
                    "model": "text-embedding-3-small", "input": texts, "dimensions": self.dimension,
                })
                if response.status_code != 200:
                    raise BrokerEmbeddingError(f"Broker embedding request failed (HTTP {response.status_code})")
                payload = response.json()
                if payload.get("success") is not True:
                    raise BrokerEmbeddingError("Broker embedding request failed")
                vectors = payload["data"]["embeddings"]
                if len(vectors) != len(texts) or any(
                    len(v) != self.dimension or any(type(x) not in (float, int) or not math.isfinite(x) for x in v)
                    for v in vectors
                ):
                    raise BrokerEmbeddingError("Broker returned invalid embedding dimensions")
                return vectors
        except BrokerEmbeddingError:
            raise
        except (httpx.HTTPError, OSError, ValueError, KeyError, TypeError):
            raise BrokerEmbeddingError("Broker embedding service is unavailable") from None
