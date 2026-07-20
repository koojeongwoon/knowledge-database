import asyncio
import os
import time
import unittest
from unittest.mock import AsyncMock, patch

from src.settings.oauth_session import (
    OAuthClient,
    OAuthSessionExpired,
    OAuthSessionUnavailable,
    ServerSessionStore,
)


class FakeCache:
    def __init__(self):
        self.values = {}

    def get(self, key):
        item = self.values.get(key)
        return item[0] if item else None

    def set(self, key, value, ttl=300):
        self.values[key] = (value, ttl)
        return True

    def delete(self, key):
        return self.values.pop(key, None) is not None


class OAuthSessionTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.previous_key = os.environ.get("SETTINGS_ENCRYPTION_KEY")
        os.environ["SETTINGS_ENCRYPTION_KEY"] = "unit-test-session-key"
        self.cache = FakeCache()
        self.oauth = AsyncMock(spec=OAuthClient)
        self.store = ServerSessionStore(cache=self.cache, oauth_client=self.oauth)

    def tearDown(self):
        if self.previous_key is None:
            os.environ.pop("SETTINGS_ENCRYPTION_KEY", None)
        else:
            os.environ["SETTINGS_ENCRYPTION_KEY"] = self.previous_key

    def test_login_state_is_one_time_and_verifier_is_not_stored_in_plaintext(self):
        state, verifier, challenge = self.store.begin_login()
        stored = next(iter(self.cache.values.values()))[0]
        self.assertNotIn(verifier, stored)
        self.assertTrue(challenge)
        self.assertEqual(self.store.consume_login(state), verifier)
        with self.assertRaises(OAuthSessionExpired):
            self.store.consume_login(state)

    @patch("src.settings.oauth_session.verify_auth_token")
    async def test_expiring_access_token_is_refreshed_and_rotated(self, verify_token):
        now = int(time.time())
        verify_token.side_effect = [
            {"sub": "auth-user", "exp": now + 1},
            {"sub": "auth-user", "exp": now + 3600},
        ]
        session_id = self.store.create({"access_token": "old-access", "refresh_token": "old-refresh"})
        self.oauth.refresh.return_value = {"access_token": "new-access", "refresh_token": "new-refresh"}

        resolved = await self.store.resolve(session_id)

        self.assertEqual(resolved.access_token, "new-access")
        self.assertEqual(resolved.refresh_token, "new-refresh")
        self.oauth.refresh.assert_awaited_once_with("old-refresh")

    @patch("src.settings.oauth_session.verify_auth_token")
    async def test_concurrent_requests_only_refresh_once(self, verify_token):
        now = int(time.time())
        verify_token.side_effect = [
            {"sub": "auth-user", "exp": now + 1},
            {"sub": "auth-user", "exp": now + 3600},
        ]
        session_id = self.store.create({"access_token": "old-access", "refresh_token": "old-refresh"})

        async def refresh(_):
            await asyncio.sleep(0.02)
            return {"access_token": "new-access", "refresh_token": "new-refresh"}

        self.oauth.refresh.side_effect = refresh
        first, second = await asyncio.gather(self.store.resolve(session_id), self.store.resolve(session_id))
        self.assertEqual(first.refresh_token, "new-refresh")
        self.assertEqual(second.refresh_token, "new-refresh")
        self.oauth.refresh.assert_awaited_once()

    @patch("src.settings.oauth_session.verify_auth_token")
    async def test_temporary_auth_outage_keeps_still_valid_access_token(self, verify_token):
        now = int(time.time())
        verify_token.return_value = {"sub": "auth-user", "exp": now + 60}
        session_id = self.store.create({"access_token": "old-access", "refresh_token": "old-refresh"})
        self.oauth.refresh.side_effect = OAuthSessionUnavailable("temporary outage")

        resolved = await self.store.resolve(session_id)

        self.assertEqual(resolved.access_token, "old-access")

    @patch("src.settings.oauth_session.verify_auth_token")
    async def test_rejected_refresh_revokes_local_session(self, verify_token):
        now = int(time.time())
        verify_token.return_value = {"sub": "auth-user", "exp": now + 1}
        session_id = self.store.create({"access_token": "old-access", "refresh_token": "old-refresh"})
        self.oauth.refresh.side_effect = OAuthSessionExpired("revoked")

        with self.assertRaises(OAuthSessionExpired):
            await self.store.resolve(session_id)
        with self.assertRaises(OAuthSessionExpired):
            await self.store.resolve(session_id)

    @patch("src.settings.oauth_session.verify_auth_token")
    async def test_logout_revokes_refresh_token_before_local_session(self, verify_token):
        now = int(time.time())
        verify_token.return_value = {"sub": "auth-user", "exp": now + 3600}
        self.oauth.logout_url.return_value = "https://auth.example/connect/logout"
        session_id = self.store.create({
            "access_token": "access",
            "refresh_token": "refresh",
            "id_token": "id-token",
        })

        logout_url, remotely_revoked = await self.store.logout(session_id)

        self.assertTrue(remotely_revoked)
        self.assertEqual(logout_url, "https://auth.example/connect/logout")
        self.oauth.revoke.assert_awaited_once_with("refresh")
        self.oauth.logout_url.assert_called_once_with("id-token")
        with self.assertRaises(OAuthSessionExpired):
            await self.store.resolve(session_id)

    @patch("src.settings.oauth_session.verify_auth_token")
    async def test_logout_deletes_local_session_when_remote_revocation_fails(self, verify_token):
        now = int(time.time())
        verify_token.return_value = {"sub": "auth-user", "exp": now + 3600}
        self.oauth.revoke.side_effect = OAuthSessionUnavailable("temporary outage")
        self.oauth.logout_url.return_value = "https://auth.example/connect/logout"
        session_id = self.store.create({
            "access_token": "access",
            "refresh_token": "refresh",
            "id_token": "id-token",
        })

        _, remotely_revoked = await self.store.logout(session_id)

        self.assertFalse(remotely_revoked)
        with self.assertRaises(OAuthSessionExpired):
            await self.store.resolve(session_id)

    @patch("src.settings.oauth_session.verify_auth_token")
    async def test_logout_refreshes_legacy_session_to_obtain_id_token(self, verify_token):
        now = int(time.time())
        verify_token.side_effect = [
            {"sub": "auth-user", "exp": now + 3600},
            {"sub": "auth-user", "exp": now + 3600},
        ]
        self.oauth.refresh.return_value = {
            "access_token": "new-access",
            "refresh_token": "new-refresh",
            "id_token": "new-id-token",
        }
        self.oauth.logout_url.return_value = "https://auth.example/connect/logout"
        session_id = self.store.create({
            "access_token": "old-access",
            "refresh_token": "old-refresh",
        })

        _, remotely_revoked = await self.store.logout(session_id)

        self.assertTrue(remotely_revoked)
        self.oauth.refresh.assert_awaited_once_with("old-refresh")
        self.oauth.revoke.assert_awaited_once_with("new-refresh")
        self.oauth.logout_url.assert_called_once_with("new-id-token")


if __name__ == "__main__":
    unittest.main()
