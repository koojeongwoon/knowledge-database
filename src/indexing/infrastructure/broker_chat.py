"""Structured LLM output over Broker SSE; no provider credentials or IAM bundle."""
import json,os
from datetime import datetime,timezone
from pathlib import Path
from uuid import uuid4
import httpx
from src.settings.broker_identity import BrokerIdentityRepository
from .broker_embedding import BrokerEmbeddingError


def strict_schema(model):
    schema=model.model_json_schema()
    def visit(node):
        if isinstance(node,dict):
            if node.get('type')=='object':
                node['additionalProperties']=False
                node['required']=list(node.get('properties',{}))
            for value in node.values():visit(value)
        elif isinstance(node,list):
            for value in node:visit(value)
    visit(schema)
    return schema


class BrokerStructuredChat:
    def __init__(self,owner_id,identity_repository=None,transport=None,auth_type="api_key"):
        self.auth_type=auth_type
        self.subject=(identity_repository or BrokerIdentityRepository()).subject_for_owner(owner_id)
        self.transport=transport

    def parse(self,model,messages,response_format,temperature):
        base=os.getenv('CREDENTIAL_BROKER_URL','').rstrip('/')
        if not base:raise BrokerEmbeddingError('Broker URL is not configured')
        try:
            workload=Path(os.getenv('BROKER_WORKLOAD_TOKEN_FILE','/var/run/secrets/credential-broker/token')).read_text().strip()
            if not workload:raise BrokerEmbeddingError('Broker workload token missing')
            payload={'subject':self.subject,'issued_at':datetime.now(timezone.utc).isoformat(),
                'request_id':str(uuid4()),'model':model,'messages':messages,'auth_type':self.auth_type,
                'temperature':None if self.auth_type=='openai_oauth' else temperature,
                'max_completion_tokens':4096,'action':'llm.chat','response_format':{
                    'type':'json_schema','json_schema':{'name':response_format.__name__,
                    'strict':True,'schema':strict_schema(response_format)}}}
            content=[];done=False;finish=None;size=0
            with httpx.Client(timeout=95,follow_redirects=False,transport=self.transport) as client:
                with client.stream('POST',base+'/v1/workload/chat/completions',
                        headers={'Authorization':'Bearer '+workload},json=payload) as response:
                    if response.status_code!=200:raise BrokerEmbeddingError('Broker LLM request failed',response.status_code)
                    for line in response.iter_lines():
                        size+=len(line)
                        if size>4000000:raise BrokerEmbeddingError('Broker LLM response too large')
                        if not line.startswith('data:'):continue
                        value=line[5:].strip()
                        if value=='[DONE]':done=True;break
                        event=json.loads(value)
                        if 'code' in event or 'error' in event:raise BrokerEmbeddingError('Broker LLM stream failed')
                        for choice in event.get('choices',[]):
                            if choice.get('index')!=0:continue
                            delta=choice.get('delta',{})
                            if delta.get('refusal'):raise BrokerEmbeddingError('Broker LLM response refused')
                            if delta.get('content'):content.append(delta['content'])
                            finish=choice.get('finish_reason') or finish
            if not done or finish!='stop':raise BrokerEmbeddingError('Broker LLM response incomplete')
            return response_format.model_validate_json(''.join(content))
        except BrokerEmbeddingError:raise
        except (httpx.HTTPError,OSError,ValueError,KeyError,TypeError):
            raise BrokerEmbeddingError('Broker LLM service unavailable') from None

    def linked(self):
        base=os.getenv('CREDENTIAL_BROKER_URL','').rstrip('/')
        if not base:raise BrokerEmbeddingError('Broker URL is not configured')
        try:
            token=Path(os.getenv('BROKER_WORKLOAD_TOKEN_FILE','/var/run/secrets/credential-broker/token')).read_text().strip()
            with httpx.Client(timeout=35,follow_redirects=False,transport=self.transport) as client:
                response=client.post(base+'/v1/workload/codex/status',headers={'Authorization':'Bearer '+token},
                    json={'subject':self.subject,'issued_at':datetime.now(timezone.utc).isoformat()})
            if response.status_code!=200:raise BrokerEmbeddingError('Broker connection status unavailable')
            return response.json().get('linked') is True
        except (httpx.HTTPError,OSError,ValueError):
            raise BrokerEmbeddingError('Broker connection status unavailable') from None
