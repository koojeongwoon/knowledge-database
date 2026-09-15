"""Codex OAuth lifecycle through Broker; no provider token enters Knowledge."""
import os
from datetime import datetime, timezone
from pathlib import Path

import httpx

from src.settings.broker_identity import BrokerIdentityRepository


class BrokerCodexClient:
    def __init__(self, owner_id, identity_repository=None, transport=None):
        self.subject=(identity_repository or BrokerIdentityRepository()).subject_for_owner(owner_id)
        self.base=os.getenv('CREDENTIAL_BROKER_URL','').rstrip('/')
        self.token_file=Path(os.getenv('BROKER_WORKLOAD_TOKEN_FILE','/var/run/secrets/credential-broker/token'))
        self.transport=transport

    def _body(self):
        return {'subject':self.subject,'issued_at':datetime.now(timezone.utc).isoformat()}

    async def _post(self,path,body):
        if not self.base:raise RuntimeError('Broker URL is not configured')
        token=self.token_file.read_text().strip()
        if not token:raise RuntimeError('Broker workload token is missing')
        try:
            async with httpx.AsyncClient(timeout=35,follow_redirects=False,trust_env=False,transport=self.transport) as client:
                response=await client.post(self.base+path,headers={'Authorization':'Bearer '+token},json=body)
            if response.status_code!=200:raise RuntimeError('Broker Codex lifecycle request failed')
            return response.json()
        except (httpx.HTTPError,OSError,ValueError):
            raise RuntimeError('Broker Codex lifecycle unavailable') from None

    async def start_device_flow(self):
        return await self._post('/v1/workload/codex/device/start',self._body())

    async def check_device_token(self,device_code,user_code):
        return await self._post('/v1/workload/codex/device/check',self._body()|{
            'device_auth_id':device_code,'user_code':user_code,
        })

    async def unlink(self):
        return await self._post('/v1/workload/codex/unlink',self._body())
