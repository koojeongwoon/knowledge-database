"""Service-side authenticated HTTP client. Request/response bodies stay opaque to Broker."""
import base64,json,os
from contextlib import contextmanager
from datetime import datetime,timezone
from pathlib import Path
from uuid import uuid4
import httpx

class BrokerTransportError(RuntimeError):
    def __init__(self,message,status_code=502):super().__init__(message);self.status_code=status_code

class BrokerHttpClient:
    def __init__(self,subject,transport=None):self.subject=subject;self.transport=transport

    @contextmanager
    def stream(self,kind,profile,method,url,*,headers=None,content=b'',connection_id=None,
               credential_version=None,retry_authentication=False):
        base=os.getenv('CREDENTIAL_BROKER_URL','').rstrip('/')
        if not base:raise BrokerTransportError('Broker URL is not configured')
        try:
            for attempt in range(2 if retry_authentication else 1):
                token=Path(os.getenv('BROKER_WORKLOAD_TOKEN_FILE','/var/run/secrets/credential-broker/token')).read_text().strip()
                if not token:raise BrokerTransportError('Broker workload token is missing')
                metadata={'request_id':str(uuid4()),'subject':self.subject,'issued_at':datetime.now(timezone.utc).isoformat(),
                    'profile':profile,'method':method,'url':url,'headers':headers or {}}
                if connection_id:metadata['connection_id']=connection_id
                if credential_version is not None:metadata['credential_version']=credential_version
                encoded=base64.b64encode(json.dumps(metadata).encode()).decode()
                with httpx.Client(timeout=125,follow_redirects=False,trust_env=False,transport=self.transport) as client:
                    with client.stream('POST',base+'/v1/proxy/'+kind,headers={'Authorization':'Bearer '+token,
                            'X-Broker-Request':encoded,'X-Request-ID':metadata['request_id']},content=content) as response:
                        if response.headers.get('x-broker-response-source')!='upstream':
                            raise BrokerTransportError('Broker rejected forwarding request',response.status_code)
                        if (attempt==0 and retry_authentication and response.status_code==401
                            and response.headers.get('x-broker-oauth-refreshed')=='true'):
                            # Only call sites that can safely repeat their operation opt in.
                            continue
                        yield response
                        return
        except BrokerTransportError:raise
        except (httpx.HTTPError,OSError,ValueError):raise BrokerTransportError('Broker transport unavailable') from None
