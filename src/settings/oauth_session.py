import asyncio
import base64
import hashlib
import json
import os
import secrets
import time
from dataclasses import asdict, dataclass
from typing import Dict, Optional
from urllib.parse import urlencode

import httpx
import jwt
from cryptography.fernet import Fernet, InvalidToken

from src.api_keys.auth import AUTH_SERVER_URL, verify_auth_token
from src.core.cache.factory import AppCacheManager


class OAuthSessionError(RuntimeError):
    pass


class OAuthSessionUnavailable(OAuthSessionError):
    pass


class OAuthSessionExpired(OAuthSessionError):
    pass


@dataclass
class TokenSet:
    access_token: str
    refresh_token: str
    access_token_expires_at: int
    absolute_expires_at: int
    auth_id: str
    id_token: str = ""


class OAuthClient:
    def __init__(self):
        self.client_id = os.getenv("KNOWLEDGE_CLIENT_ID", "knowledge-service")
        self.client_secret = os.getenv("KNOWLEDGE_CLIENT_SECRET", "")
        self.authorization_endpoint = os.getenv(
            "AUTHORIZATION_ENDPOINT", f"{AUTH_SERVER_URL}/oauth2/authorize"
        )
        self.token_endpoint = os.getenv("TOKEN_ENDPOINT", f"{AUTH_SERVER_URL}/oauth2/token")
        self.revocation_endpoint = os.getenv(
            "TOKEN_REVOCATION_ENDPOINT", f"{AUTH_SERVER_URL}/oauth2/revoke"
        )
        self.end_session_endpoint = os.getenv(
            "OIDC_END_SESSION_ENDPOINT", f"{AUTH_SERVER_URL}/connect/logout"
        )
        self.redirect_uri = os.getenv(
            "KNOWLEDGE_REDIRECT_URI", "https://knowledge.lynply.com/callback"
        )
        self.scope = os.getenv("KNOWLEDGE_OAUTH_SCOPE", "openid profile email")
        self.post_logout_redirect_uri = os.getenv(
            "KNOWLEDGE_POST_LOGOUT_REDIRECT_URI",
            "https://knowledge.lynply.com/logged-out",
        )
        self.timeout = float(os.getenv("AUTH_HTTP_TIMEOUT_SECONDS", "10"))

    def authorization_url(self, state: str, code_challenge: str) -> str:
        query = urlencode({
            'response_type': 'code',
            'client_id': self.client_id,
            'redirect_uri': self.redirect_uri,
            'scope': self.scope,
            'state': state,
            'code_challenge': code_challenge,
            'code_challenge_method': 'S256',
        })
        return f"{self.authorization_endpoint}?{query}"

    async def exchange_code(self, code: str, code_verifier: str) -> dict:
        return await self._token_request({
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": self.redirect_uri,
            "code_verifier": code_verifier,
        })

    async def refresh(self, refresh_token: str) -> dict:
        return await self._token_request({
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
        })

    async def revoke(self, token: str, token_type_hint: str = "refresh_token") -> None:
        if not self.client_secret:
            raise OAuthSessionUnavailable("KNOWLEDGE_CLIENT_SECRET가 설정되지 않았습니다.")
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.post(
                    self.revocation_endpoint,
                    data={"token": token, "token_type_hint": token_type_hint},
                    auth=(self.client_id, self.client_secret),
                    headers={"Accept": "application/json"},
                )
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise OAuthSessionUnavailable("인증서버 토큰 폐기에 실패했습니다.") from exc

    def logout_url(self, id_token: str) -> str:
        query = urlencode({
            "id_token_hint": id_token,
            "post_logout_redirect_uri": self.post_logout_redirect_uri,
        })
        return f"{self.end_session_endpoint}?{query}"

    async def _token_request(self, data: dict) -> dict:
        if not self.client_secret:
            raise OAuthSessionUnavailable("KNOWLEDGE_CLIENT_SECRET가 설정되지 않았습니다.")
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.post(
                    self.token_endpoint,
                    data=data,
                    auth=(self.client_id, self.client_secret),
                    headers={"Accept": "application/json"},
                )
            if response.status_code in (400, 401):
                raise OAuthSessionExpired("인증 자격 증명이 만료되었거나 폐기되었습니다.")
            response.raise_for_status()
            payload = response.json()
        except OAuthSessionExpired:
            raise
        except (httpx.HTTPError, ValueError) as exc:
            raise OAuthSessionUnavailable("인증서버 토큰 요청에 실패했습니다.") from exc
        if not payload.get("access_token") or not payload.get("refresh_token"):
            raise OAuthSessionUnavailable("인증서버 토큰 응답이 올바르지 않습니다.")
        return payload


class ServerSessionStore:
    SESSION_PREFIX = "knowledge:web-session:"
    LOGIN_PREFIX = "knowledge:oauth-login:"
    LOCK_PREFIX = "knowledge:web-session-lock:"

    def __init__(self, cache=None, oauth_client=None):
        self.cache = cache or AppCacheManager()
        self.oauth_client = oauth_client or OAuthClient()
        self.session_ttl = int(os.getenv("KNOWLEDGE_SESSION_TTL_SECONDS", "2592000"))
        self.login_ttl = int(os.getenv("OAUTH_LOGIN_TTL_SECONDS", "300"))
        self.refresh_skew = int(os.getenv("ACCESS_TOKEN_REFRESH_SKEW_SECONDS", "120"))
        self._locks: Dict[str, asyncio.Lock] = {}

    def begin_login(self) -> tuple[str, str, str]:
        state = secrets.token_urlsafe(32)
        verifier = secrets.token_urlsafe(64)
        challenge = base64.urlsafe_b64encode(
            hashlib.sha256(verifier.encode("ascii")).digest()
        ).rstrip(b"=").decode("ascii")
        if not self.cache.set(self.LOGIN_PREFIX + self._hash(state), self._encrypt(verifier), self.login_ttl):
            raise OAuthSessionUnavailable("로그인 상태 저장소를 사용할 수 없습니다.")
        return state, verifier, challenge

    def consume_login(self, state: str) -> str:
        key = self.LOGIN_PREFIX + self._hash(state)
        client = getattr(self.cache, "client", None)
        encrypted = client.getdel(key) if client is not None else self.cache.get(key)
        if client is None and encrypted:
            self.cache.delete(key)
        if not encrypted:
            raise OAuthSessionExpired("로그인 요청이 만료되었거나 이미 사용되었습니다.")
        return self._decrypt(encrypted)

    def create(self, token_payload: dict) -> str:
        token_set = self._validated_token_set(token_payload)
        session_id = secrets.token_urlsafe(32)
        ttl = max(1, token_set.absolute_expires_at - int(time.time()))
        if not self.cache.set(
            self.SESSION_PREFIX + self._hash(session_id),
            self._encrypt(json.dumps(asdict(token_set), separators=(",", ":"))),
            ttl,
        ):
            raise OAuthSessionUnavailable("로그인 세션 저장소를 사용할 수 없습니다.")
        return session_id

    async def resolve(self, session_id: str) -> TokenSet:
        token_set = self._load(session_id)
        now = int(time.time())
        if token_set.absolute_expires_at <= now:
            self.revoke(session_id)
            raise OAuthSessionExpired("로그인 세션이 만료되었습니다.")
        if token_set.access_token_expires_at - now > self.refresh_skew:
            return token_set
        lock = self._locks.setdefault(self._hash(session_id), asyncio.Lock())
        async with lock:
            token_set = self._load(session_id)
            now = int(time.time())
            if token_set.access_token_expires_at - now > self.refresh_skew:
                return token_set
            return await self._refresh(session_id, token_set)

    async def _refresh(self, session_id: str, current: TokenSet) -> TokenSet:
        distributed_token = secrets.token_urlsafe(16)
        lock_key = self.LOCK_PREFIX + self._hash(session_id)
        client = getattr(self.cache, "client", None)
        acquired = True
        if client is not None:
            try:
                acquired = bool(client.set(lock_key, distributed_token, nx=True, ex=15))
            except Exception as exc:
                raise OAuthSessionUnavailable("세션 잠금 저장소를 사용할 수 없습니다.") from exc
        if not acquired:
            for _ in range(20):
                await asyncio.sleep(0.1)
                latest = self._load(session_id)
                if latest.refresh_token != current.refresh_token:
                    return latest
            raise OAuthSessionUnavailable("세션 갱신이 진행 중입니다. 잠시 후 다시 시도해 주세요.")
        try:
            payload = await self.oauth_client.refresh(current.refresh_token)
            refreshed = self._validated_token_set(
                payload,
                absolute_expires_at=current.absolute_expires_at,
                id_token=current.id_token,
            )
            ttl = max(1, refreshed.absolute_expires_at - int(time.time()))
            if not self.cache.set(
                self.SESSION_PREFIX + self._hash(session_id),
                self._encrypt(json.dumps(asdict(refreshed), separators=(",", ":"))),
                ttl,
            ):
                raise OAuthSessionUnavailable("갱신된 세션을 저장하지 못했습니다.")
            return refreshed
        except OAuthSessionUnavailable:
            if current.access_token_expires_at > int(time.time()):
                return current
            raise
        except OAuthSessionExpired:
            self.revoke(session_id)
            raise
        finally:
            if client is not None and acquired:
                try:
                    client.eval(
                        "if redis.call('get', KEYS[1]) == ARGV[1] "
                        "then return redis.call('del', KEYS[1]) else return 0 end",
                        1,
                        lock_key,
                        distributed_token,
                    )
                except Exception:
                    pass

    def revoke(self, session_id: str) -> None:
        self.cache.delete(self.SESSION_PREFIX + self._hash(session_id))

    async def logout(self, session_id: str) -> tuple[str, bool]:
        token_set = self._load(session_id)
        remotely_revoked = False
        try:
            if not token_set.id_token:
                payload = await self.oauth_client.refresh(token_set.refresh_token)
                token_set = self._validated_token_set(
                    payload,
                    absolute_expires_at=token_set.absolute_expires_at,
                )
            await self.oauth_client.revoke(token_set.refresh_token)
            remotely_revoked = True
        except OAuthSessionError:
            pass
        finally:
            self.revoke(session_id)
        return self.oauth_client.logout_url(token_set.id_token), remotely_revoked

    def _load(self, session_id: str) -> TokenSet:
        encrypted = self.cache.get(self.SESSION_PREFIX + self._hash(session_id))
        if not encrypted:
            raise OAuthSessionExpired("로그인 세션이 없거나 만료되었습니다.")
        try:
            return TokenSet(**json.loads(self._decrypt(encrypted)))
        except (TypeError, ValueError, KeyError, json.JSONDecodeError) as exc:
            raise OAuthSessionExpired("로그인 세션을 읽을 수 없습니다.") from exc

    def _validated_token_set(
        self,
        payload: dict,
        absolute_expires_at: Optional[int] = None,
        id_token: str = "",
    ) -> TokenSet:
        try:
            claims = verify_auth_token(payload["access_token"])
            expires_at = int(claims["exp"])
            refresh_token = payload["refresh_token"]
        except (KeyError, TypeError, ValueError, jwt.PyJWTError) as exc:
            raise OAuthSessionUnavailable("인증서버 토큰 응답이 올바르지 않습니다.") from exc
        now = int(time.time())
        return TokenSet(
            access_token=payload["access_token"],
            refresh_token=refresh_token,
            access_token_expires_at=expires_at,
            absolute_expires_at=absolute_expires_at or now + self.session_ttl,
            auth_id=claims["sub"],
            id_token=payload.get("id_token") or id_token,
        )

    @staticmethod
    def _hash(value: str) -> str:
        return hashlib.sha256(value.encode("utf-8")).hexdigest()

    @staticmethod
    def _cipher() -> Fernet:
        master = os.getenv("SESSION_ENCRYPTION_KEY", "").strip() or os.getenv(
            "SETTINGS_ENCRYPTION_KEY", ""
        ).strip()
        if not master:
            raise OAuthSessionUnavailable("SETTINGS_ENCRYPTION_KEY가 설정되지 않았습니다.")
        key = base64.urlsafe_b64encode(hashlib.sha256(master.encode("utf-8")).digest())
        return Fernet(key)

    def _encrypt(self, value: str) -> str:
        return self._cipher().encrypt(value.encode("utf-8")).decode("ascii")

    def _decrypt(self, value: str) -> str:
        try:
            return self._cipher().decrypt(value.encode("ascii")).decode("utf-8")
        except InvalidToken as exc:
            raise OAuthSessionExpired("로그인 세션을 복호화할 수 없습니다.") from exc


_session_store: Optional[ServerSessionStore] = None


def session_store() -> ServerSessionStore:
    global _session_store
    if _session_store is None:
        _session_store = ServerSessionStore()
    return _session_store
