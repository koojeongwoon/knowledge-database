"""Codex OAuth lifecycle through Broker; no provider token enters Knowledge."""
import os
from datetime import datetime, timezone
from pathlib import Path

import httpx
from src.core.http import async_http_client, broker_base_url, workload_token_path

from src.settings.broker_identity import BrokerIdentityRepository


class BrokerCodexClient:
    def __init__(self, owner_id, identity_repository=None, transport=None):
        self.subject=(identity_repository or BrokerIdentityRepository()).subject_for_owner(owner_id)
        self.base=broker_base_url()
        self.token_file=workload_token_path()
        self.transport=transport

    def _body(self):
        return {'subject':self.subject,'issued_at':datetime.now(timezone.utc).isoformat()}

    async def _post(self,path,body):
        if not self.base:raise RuntimeError('Broker URL is not configured')
        token=self.token_file.read_text().strip()
        if not token:raise RuntimeError('Broker workload token is missing')
        try:
            async with async_http_client('control',transport=self.transport) as client:
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
