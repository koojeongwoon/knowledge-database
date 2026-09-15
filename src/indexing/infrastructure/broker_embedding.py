"""Knowledge builds OpenAI embedding requests; Broker only authenticates transport."""
import json,math
from src.indexing.domain.embedding import BaseEmbeddingService
from src.settings.broker_identity import BrokerIdentityRepository
from .broker_http import BrokerHttpClient,BrokerTransportError
BrokerEmbeddingError=BrokerTransportError

class WorkloadBrokerEmbeddingService(BaseEmbeddingService):
    def __init__(self,owner_id,dimension=1536,identity_repository=None,transport=None,connection_id=None,credential_version=None):
        self.subject=(identity_repository or BrokerIdentityRepository()).subject_for_owner(owner_id)
        self.dimension=dimension;self.connection_id=connection_id;self.credential_version=credential_version
        self.client=BrokerHttpClient(self.subject,transport)

    def get_dimension(self):return self.dimension
    def embed_text(self,text):return self.embed_batch([text])[0]

    def embed_batch(self,texts,batch_size=100):
        if not 1<=batch_size<=100:raise ValueError('Embedding batch size must be between 1 and 100')
        if any(not text.strip() or len(text)>16000 for text in texts):raise BrokerEmbeddingError('Invalid embedding input',400)
        output=[]
        for offset in range(0,len(texts),batch_size):output.extend(self._execute(texts[offset:offset+batch_size]))
        return output

    def _execute(self,texts):
        if sum(map(len,texts))>100000:raise BrokerEmbeddingError('Embedding batch too large',400)
        payload={'model':'text-embedding-3-small','input':texts,'dimensions':self.dimension,'encoding_format':'float'}
        with self.client.stream('api-key','openai','POST','https://api.openai.com/v1/embeddings',
                headers={'content-type':'application/json'},content=json.dumps(payload).encode(),
                connection_id=self.connection_id,credential_version=self.credential_version) as response:
            if response.status_code!=200:raise BrokerEmbeddingError('OpenAI embedding request failed',response.status_code)
            try:
                response.read();body=response.json();data=sorted(body['data'],key=lambda item:item['index'])
                if [item['index'] for item in data]!=list(range(len(texts))):raise ValueError()
                vectors=[item['embedding'] for item in data]
                if any(len(v)!=self.dimension or any(type(x) not in (float,int) or not math.isfinite(x) for x in v) for v in vectors):raise ValueError()
                return vectors
            except (ValueError,KeyError,TypeError):raise BrokerEmbeddingError('Invalid OpenAI embedding response') from None


def create_user_embedding_service(dimension=1536):
    from src.core.config import current_user_config
    owner=(current_user_config.get() or {}).get('user_id')
    if not owner or owner=='SYSTEM':raise BrokerEmbeddingError('Verified Knowledge owner context required',401)
    return WorkloadBrokerEmbeddingService(owner,dimension)
