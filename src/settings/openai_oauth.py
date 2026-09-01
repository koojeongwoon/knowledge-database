import asyncio
import os
import time
from dataclasses import dataclass
from typing import Any, Dict, Optional

import httpx


class OpenAIOAuthError(RuntimeError):
    pass


class OpenAIOAuthPending(OpenAIOAuthError):
    pass


class OpenAIOAuthSlowDown(OpenAIOAuthError):
    pass


class OpenAIOAuthExpired(OpenAIOAuthError):
    pass


class OpenAIOAuthDenied(OpenAIOAuthError):
    pass


@dataclass
class OpenAITokenSet:
    access_token: str
    refresh_token: str
    expires_at: int  # Unix timestamp in seconds
    token_type: str = "Bearer"
    scope: str = ""
    id_token: str = ""


class OpenAIOAuthClient:
    """
    OpenAI OAuth 2.0 Device Authorization Grant 및 토큰 갱신 클라이언트.
    ChatGPT Plus/Pro 계정의 인증 토큰을 획득하고 관리합니다.
    """

    def __init__(
        self,
        client_id: Optional[str] = None,
        device_endpoint: Optional[str] = None,
        device_token_endpoint: Optional[str] = None,
        token_endpoint: Optional[str] = None,
        scope: Optional[str] = None,
        timeout: float = 15.0,
    ):
        self.client_id = client_id or os.getenv("OPENAI_OAUTH_CLIENT_ID", "app_EMoamEEZ73f0CkXaXp7hrann")
        self.device_endpoint = device_endpoint or os.getenv(
            "OPENAI_DEVICE_AUTH_ENDPOINT", "https://auth.openai.com/api/accounts/deviceauth/usercode"
        )
        self.device_token_endpoint = device_token_endpoint or os.getenv(
            "OPENAI_DEVICE_TOKEN_ENDPOINT", "https://auth.openai.com/api/accounts/deviceauth/token"
        )
        self.token_endpoint = token_endpoint or os.getenv(
            "OPENAI_TOKEN_ENDPOINT", "https://auth.openai.com/oauth/token"
        )
        self.scope = scope or os.getenv(
            "OPENAI_OAUTH_SCOPE", "openid profile email model.request offline_access"
        )
        self.timeout = float(os.getenv("OPENAI_OAUTH_TIMEOUT_SECONDS", str(timeout)))

    async def start_device_flow(self) -> Dict[str, Any]:
        """
        Device Authorization 요청을 보내고 user_code와 verification_uri를 받아옵니다.
        """
        payload = {
            "client_id": self.client_id,
        }
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.post(
                    self.device_endpoint,
                    json=payload,
                    headers={"Content-Type": "application/json"},
                )
        except Exception as exc:
            raise OpenAIOAuthError(f"OpenAI Device 인증 요청 네트워크 오류: {exc}") from exc

        if response.status_code != 200:
            raise OpenAIOAuthError(
                f"OpenAI Device 인증 시작 실패 (HTTP {response.status_code})"
            )

        data = response.json()
        device_auth_id = data.get("device_auth_id", "")
        user_code = data.get("user_code", "")
        interval = int(data.get("interval", 5))

        return {
            "device_code": device_auth_id,
            "device_auth_id": device_auth_id,
            "user_code": user_code,
            "verification_uri": "https://auth.openai.com/codex/device",
            "verification_uri_complete": f"https://auth.openai.com/codex/device?user_code={user_code}",
            "expires_in": 900,
            "interval": interval,
        }

    async def check_device_token(
        self, device_code: str, user_code: Optional[str] = None
    ) -> Optional[OpenAITokenSet]:
        """
        단일 폴링 요청으로 토큰 발급 여부를 확인합니다.
        대기 중이면 None을 반환하고, 완료 시 OpenAITokenSet을 반환합니다.
        """
        payload = {
            "client_id": self.client_id,
            "device_auth_id": device_code,
        }
        if user_code:
            payload["user_code"] = user_code

        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.post(
                    self.device_token_endpoint,
                    json=payload,
                    headers={"Content-Type": "application/json"},
                )
        except Exception as exc:
            raise OpenAIOAuthError(f"OpenAI 토큰 폴링 네트워크 오류: {exc}") from exc

        if response.status_code == 200:
            data = response.json()
            expires_in = int(data.get("expires_in", 3600))
            return OpenAITokenSet(
                access_token=data.get("access_token", ""),
                refresh_token=data.get("refresh_token", ""),
                expires_at=int(time.time()) + expires_in,
                token_type=data.get("token_type", "Bearer"),
                scope=data.get("scope", ""),
                id_token=data.get("id_token", ""),
            )

        error_data = {}
        try:
            error_data = response.json()
        except Exception:
            pass

        err_obj = error_data.get("error", {})
        error_code = (
            err_obj.get("code")
            if isinstance(err_obj, dict)
            else error_data.get("error", str(response.status_code))
        )

        if error_code in ("deviceauth_authorization_pending", "authorization_pending"):
            return None
        elif error_code == "slow_down":
            raise OpenAIOAuthSlowDown("폴링 간격을 늘려야 합니다.")
        elif error_code in ("deviceauth_expired", "expired_token"):
            raise OpenAIOAuthExpired("인증 코드가 만료되었습니다. 다시 시도해 주세요.")
        elif error_code in ("deviceauth_access_denied", "access_denied"):
            raise OpenAIOAuthDenied("사용자가 인증을 거부했습니다.")
        else:
            err_msg = (
                err_obj.get("message")
                if isinstance(err_obj, dict) and err_obj.get("message")
                else error_code
            )
            raise OpenAIOAuthError(f"토큰 획득 실패 ({response.status_code}): {err_msg}")

    async def poll_until_complete(
        self, device_code: str, user_code: Optional[str] = None, interval: int = 5, timeout: int = 300
    ) -> OpenAITokenSet:
        """
        토큰이 발급되거나 타임아웃될 때까지 주기적으로 폴링합니다.
        """
        start_time = time.time()
        current_interval = interval

        while time.time() - start_time < timeout:
            try:
                token_set = await self.check_device_token(device_code, user_code)
                if token_set is not None:
                    return token_set
            except OpenAIOAuthSlowDown:
                current_interval += 5

            await asyncio.sleep(current_interval)

        raise OpenAIOAuthExpired(f"{timeout}초 동안 인증이 완료되지 않아 타임아웃되었습니다.")

    async def refresh_access_token(self, refresh_token: str) -> OpenAITokenSet:
        """
        refresh_token을 사용하여 새로운 access_token을 발급받습니다.
        """
        if not refresh_token:
            raise OpenAIOAuthError("refresh_token이 비어 있습니다.")

        payload = {
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            "client_id": self.client_id,
        }
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.post(
                    self.token_endpoint,
                    data=payload,
                    headers={"Content-Type": "application/x-www-form-urlencoded"},
                )
        except Exception as exc:
            raise OpenAIOAuthError(f"OpenAI 토큰 갱신 네트워크 오류: {exc}") from exc

        if response.status_code != 200:
            raise OpenAIOAuthError(
                f"OpenAI 토큰 갱신 실패 (HTTP {response.status_code})"
            )

        data = response.json()
        expires_in = int(data.get("expires_in", 3600))
        new_refresh = data.get("refresh_token") or refresh_token
        return OpenAITokenSet(
            access_token=data.get("access_token", ""),
            refresh_token=new_refresh,
            expires_at=int(time.time()) + expires_in,
            token_type=data.get("token_type", "Bearer"),
            scope=data.get("scope", ""),
            id_token=data.get("id_token", ""),
        )

    def refresh_access_token_sync(self, refresh_token: str) -> OpenAITokenSet:
        """
        동기 컨텍스트에서 refresh_token을 사용하여 새로운 access_token을 발급받습니다.
        """
        if not refresh_token:
            raise OpenAIOAuthError("refresh_token이 비어 있습니다.")

        payload = {
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            "client_id": self.client_id,
        }
        try:
            with httpx.Client(timeout=self.timeout) as client:
                response = client.post(
                    self.token_endpoint,
                    data=payload,
                    headers={"Content-Type": "application/x-www-form-urlencoded"},
                )
        except Exception as exc:
            raise OpenAIOAuthError(f"OpenAI 토큰 갱신 네트워크 오류: {exc}") from exc

        if response.status_code != 200:
            raise OpenAIOAuthError(
                f"OpenAI 토큰 갱신 실패 (HTTP {response.status_code})"
            )

        data = response.json()
        expires_in = int(data.get("expires_in", 3600))
        new_refresh = data.get("refresh_token") or refresh_token
        return OpenAITokenSet(
            access_token=data.get("access_token", ""),
            refresh_token=new_refresh,
            expires_at=int(time.time()) + expires_in,
            token_type=data.get("token_type", "Bearer"),
            scope=data.get("scope", ""),
            id_token=data.get("id_token", ""),
        )
