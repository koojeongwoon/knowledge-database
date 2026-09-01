import os
from typing import Optional, Dict, Any
import httpx


class IAMCodexClient:
    """
    IAM 인증 서버(/api/v1/codex/token)로부터 유효한 Codex Access Token을 조회하는 클라이언트.
    개인 토큰 1순위 -> 조직 토큰 Fallback 및 Redis 캐싱/자동 갱신은 IAM 서버가 전담합니다.
    """

    def __init__(self, iam_base_url: Optional[str] = None, timeout: float = 5.0):
        self.base_url = (iam_base_url or os.getenv("IAM_SERVER_URL", "http://localhost:8080")).rstrip("/")
        self.timeout = float(os.getenv("IAM_CLIENT_TIMEOUT_SECONDS", str(timeout)))

    def get_ai_bundle(self, user_id: Optional[str] = None, org_id: Optional[str] = None) -> Optional[Dict[str, Any]]:
        """
        IAM 중앙 인증 서버에서 유저/조직의 AI 자격증명 번들(Codex Token + OpenAI Key + Embedding Key)을 원스톱 조회합니다.
        """
        params = {}
        if user_id:
            params["user_id"] = user_id
        if org_id:
            params["org_id"] = org_id

        try:
            with httpx.Client(timeout=self.timeout) as client:
                response = client.get(f"{self.base_url}/api/v1/credentials/ai-bundle", params=params)
                if response.status_code == 200:
                    return response.json()
                return None
        except Exception as exc:
            print(f"Warning: Failed to fetch AI bundle from IAM server ({self.base_url}): {exc}")
            return None

    async def get_ai_bundle_async(self, user_id: Optional[str] = None, org_id: Optional[str] = None) -> Optional[Dict[str, Any]]:
        """
        비동기 방식으로 AI 자격증명 번들을 원스톱 조회합니다.
        """
        params = {}
        if user_id:
            params["user_id"] = user_id
        if org_id:
            params["org_id"] = org_id

        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.get(f"{self.base_url}/api/v1/credentials/ai-bundle", params=params)
                if response.status_code == 200:
                    return response.json()
                return None
        except Exception as exc:
            print(f"Warning: Failed to fetch AI bundle from IAM server ({self.base_url}): {exc}")
            return None

    def get_valid_token(self, user_id: Optional[str] = None, org_id: Optional[str] = None) -> Optional[Dict[str, Any]]:
        """
        동기 방식으로 IAM 서버에서 유효한 코덱스 액세스 토큰을 조회합니다.
        """
        params = {}
        if user_id:
            params["user_id"] = user_id
        if org_id:
            params["org_id"] = org_id

        try:
            with httpx.Client(timeout=self.timeout) as client:
                response = client.get(f"{self.base_url}/api/v1/codex/token", params=params)
                if response.status_code == 200:
                    return response.json()
                return None
        except Exception as exc:
            # IAM 서버 통신 실패 시 로그 남기고 None 반환
            print(f"Warning: Failed to fetch Codex token from IAM server ({self.base_url}): {exc}")
            return None

    async def start_device_flow(self) -> Dict[str, Any]:
        """
        IAM 서버에 Device Auth 시작 요청을 보냅니다.
        """
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.post(f"{self.base_url}/api/v1/codex/device/start")
            if response.status_code != 200:
                raise RuntimeError(f"IAM 서버 Device Auth 시작 실패 (HTTP {response.status_code})")
            return response.json()

    async def check_device_token(
        self,
        device_code: str,
        user_code: Optional[str] = None,
        user_id: Optional[str] = None,
        org_id: Optional[str] = None,
        account_type: str = "USER",
    ) -> Dict[str, Any]:
        """
        IAM 서버에 Device Auth 완료 여부를 확인하고 토큰을 IAM 서버에 등록하도록 요청합니다.
        """
        payload = {
            "deviceAuthId": device_code,
            "userCode": user_code,
            "userId": user_id,
            "orgId": org_id,
            "accountType": account_type,
        }
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.post(
                f"{self.base_url}/api/v1/codex/device/check",
                json=payload,
            )
            if response.status_code != 200:
                raise RuntimeError(f"IAM 서버 Device Auth 확인 실패 (HTTP {response.status_code})")
            return response.json()

    async def get_valid_token_async(self, user_id: Optional[str] = None, org_id: Optional[str] = None) -> Optional[Dict[str, Any]]:
        """
        비동기 방식으로 IAM 서버에서 유효한 코덱스 액세스 토큰을 조회합니다.
        """
        params = {}
        if user_id:
            params["user_id"] = user_id
        if org_id:
            params["org_id"] = org_id

        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.get(f"{self.base_url}/api/v1/codex/token", params=params)
                if response.status_code == 200:
                    return response.json()
                return None
        except Exception as exc:
            print(f"Warning: Failed to fetch Codex token from IAM server ({self.base_url}): {exc}")
            return None
