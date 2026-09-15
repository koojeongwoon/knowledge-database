"""Structured LLM output over Broker SSE; no provider credentials or IAM bundle."""
import json,os
from datetime import datetime,timezone
from pathlib import Path
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
        from .broker_http import BrokerHttpClient
        client=BrokerHttpClient(self.subject,self.transport)
        schema=strict_schema(response_format)
        oauth=self.auth_type=='openai_oauth'
        if oauth:
            instructions=[];inputs=[]
            for message in messages:
                if message['role'] in ('system','developer'):instructions.append(message['content'])
                else:
                    inputs.append({'role':message['role'],'content':[{'type':'output_text' if message['role']=='assistant' else 'input_text','text':message['content']}]})
            payload={'model':model,'instructions':'\n\n'.join(instructions),'input':inputs,'stream':True,'store':False,
                'text':{'format':{'type':'json_schema','name':response_format.__name__,'strict':True,'schema':schema}}}
            url='https://chatgpt.com/backend-api/codex/responses';profile='codex';kind='oauth'
        else:
            payload={'model':model,'messages':messages,'temperature':temperature,'max_completion_tokens':4096,'stream':True,'store':False,
                'response_format':{'type':'json_schema','json_schema':{'name':response_format.__name__,'strict':True,'schema':schema}}}
            url='https://api.openai.com/v1/chat/completions';profile='openai';kind='api-key'
        parts=[];done=False;finish=None;size=0
        try:
            with client.stream(kind,profile,'POST',url,headers={'content-type':'application/json'},
                    content=json.dumps(payload).encode(),retry_authentication=oauth) as response:
                if response.status_code!=200:raise BrokerEmbeddingError('LLM provider rejected request',response.status_code)
                for line in response.iter_lines():
                    size+=len(line)
                    if size>4000000:raise BrokerEmbeddingError('LLM response too large')
                    if not line.startswith('data:'):continue
                    value=line[5:].strip()
                    if value=='[DONE]':
                        if not oauth:done=True
                        break
                    event=json.loads(value)
                    if oauth:
                        event_type=event.get('type')
                        if event_type=='response.output_text.delta':parts.append(event['delta'])
                        elif event_type in ('error','response.failed','response.incomplete','response.refusal.delta'):
                            raise BrokerEmbeddingError('Codex response failed')
                        elif event_type=='response.completed':
                            result=event['response']
                            if result.get('status')!='completed' or any(item.get('type') not in ('message','reasoning') for item in result.get('output',[])):
                                raise BrokerEmbeddingError('Codex response incomplete')
                            done=True;finish='stop';break
                    else:
                        if 'error' in event:raise BrokerEmbeddingError('OpenAI response failed')
                        for choice in event.get('choices',[]):
                            if choice.get('index')!=0:continue
                            delta=choice.get('delta',{})
                            if delta.get('refusal'):raise BrokerEmbeddingError('OpenAI response refused')
                            if delta.get('content'):parts.append(delta['content'])
                            finish=choice.get('finish_reason') or finish
            if not done or finish!='stop':raise BrokerEmbeddingError('LLM response incomplete')
            return response_format.model_validate_json(''.join(parts))
        except BrokerEmbeddingError:raise
        except (ValueError,TypeError,KeyError,AttributeError):raise BrokerEmbeddingError('Invalid LLM provider response') from None

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
