import os
from pathlib import Path
import httpx
from src.indexing.infrastructure.broker_embedding import BrokerEmbeddingError

KNOWLEDGE_WORKLOADS = [
    'system:serviceaccount:llm-wiki:knowledge',
    'system:serviceaccount:llm-wiki:knowledge-indexing-worker',
]


class BrokerDelegationClient:
    def __init__(self, transport=None):
        self.transport = transport

    async def request(self, subject_token, path, payload=None):
        try:
            iam_url = os.environ['IAM_SERVER_URL'].rstrip('/')
            base_url = os.environ['CREDENTIAL_BROKER_URL'].rstrip('/')
            async with httpx.AsyncClient(timeout=15,follow_redirects=False,transport=self.transport) as client:
                response = await client.post(iam_url+'/api/auth/oauth2/token',data={
                    'grant_type':'urn:ietf:params:oauth:grant-type:token-exchange',
                    'subject_token_type':'urn:ietf:params:oauth:token-type:access_token',
                    'subject_token':subject_token,'client_id':'credential-broker',
                    'requested_target_tenant':os.getenv('IAM_TENANT_ID','ten_9664c024babc4110'),
                })
                if response.status_code != 200:
                    raise BrokerEmbeddingError('IAM token exchange failed',401)
                token = response.json()['access_token']
                workload = Path(os.getenv('BROKER_WORKLOAD_TOKEN_FILE',
                    '/var/run/secrets/credential-broker/token')).read_text().strip()
                if not subject_token or not isinstance(token, str) or not token or not workload:
                    raise BrokerEmbeddingError('Broker authentication token is missing')
                result = await client.post(base_url+path,headers={
                    'Authorization':'Bearer '+token,'X-Workload-Authorization':'Bearer '+workload,
                },json=payload)
                if result.status_code not in (200,201):
                    raise BrokerEmbeddingError('Broker delegation request failed',result.status_code)
                return result.json()['data']
        except BrokerEmbeddingError:
            raise
        except (httpx.HTTPError,OSError,ValueError,KeyError,TypeError):
            raise BrokerEmbeddingError('Broker delegation service unavailable') from None
