import asyncio
import os
import time
import unittest
from unittest.mock import AsyncMock, Mock, patch

import redis

from src.core.cache.redis import RedisCacheManager

from src.settings.oauth_session import (
    OAuthClient,
    OAuthSessionExpired,
    OAuthSessionUnavailable,
    ServerSessionStore,
)


def verified_claims(expires_at):
    return {
        "iss": "https://auth.snappytory.com/t/ten_9664c024babc4110",
        "tenant_id": "ten_9664c024babc4110",
        "sub": "auth-user",
        "email": "user@example.com",
        "name": "User",
        "user_version": "1",
        "exp": expires_at,
    }


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

    async def test_redis_failure_is_unavailable_instead_of_expired(self):
        cache = RedisCacheManager(host="127.0.0.1", port=6379)
        self.store.cache = cache
        with patch.object(cache.client, "get", side_effect=redis.ConnectionError("redis unavailable")):
            with self.assertRaises(OAuthSessionUnavailable):
                await self.store.resolve("opaque-session")
        self.oauth.refresh.assert_not_awaited()

    async def test_missing_redis_session_is_still_expired(self):
        cache = RedisCacheManager(host="127.0.0.1", port=6379)
        self.store.cache = cache
        with patch.object(cache.client, "get", return_value=None):
            with self.assertRaises(OAuthSessionExpired):
                await self.store.resolve("missing")

    def test_login_state_redis_failure_is_unavailable(self):
        self.cache.client = Mock()
        self.cache.client.getdel.side_effect = redis.TimeoutError("redis timeout")
        with self.assertRaises(OAuthSessionUnavailable):
            self.store.consume_login("state")

    @patch("src.settings.oauth_session.verify_auth_token")
    async def test_failed_refresh_preserves_session_for_retry_after_recovery(self, verify_token):
        now = int(time.time())
        verify_token.side_effect = [verified_claims(now - 1), verified_claims(now + 3600)]
        session_id = self.store.create({"access_token": "old-access", "refresh_token": "old-refresh"})
        self.oauth.refresh.side_effect = [
            OAuthSessionUnavailable("temporary outage"),
            {"access_token": "new-access", "refresh_token": "new-refresh"},
        ]
        with self.assertRaises(OAuthSessionUnavailable):
            await self.store.resolve(session_id)
        self.assertIn(self.store.SESSION_PREFIX + self.store._hash(session_id), self.cache.values)
        resolved = await self.store.resolve(session_id)
        self.assertEqual(resolved.access_token, "new-access")

    @patch("src.settings.oauth_session.verify_auth_token")
    async def test_valid_access_token_is_loaded_once_without_acquiring_lock(self, verify_token):
        verify_token.return_value = verified_claims(int(time.time()) + 3600)
        session_id = self.store.create({"access_token": "access", "refresh_token": "refresh"})
        with patch.object(self.store, "_load", wraps=self.store._load) as load:
            with patch.object(self.store, "_get_lock") as get_lock:
                resolved = await self.store.resolve(session_id)
        self.assertEqual(resolved.access_token, "access")
        load.assert_called_once_with(session_id)
        get_lock.assert_not_called()

    async def test_lock_identity_survives_churn_revoke_and_waiter_handoff(self):
        lock = self.store._get_lock("target")
        await lock.acquire()
        entered = asyncio.Event()
        finish = asyncio.Event()

        async def waiter():
            async with self.store._get_lock("target"):
                entered.set()
                await finish.wait()

        task = asyncio.create_task(waiter())
        await asyncio.sleep(0)
        lock.release()
        # Exercise the interval between release and the waiter's wakeup.
        for index in range(10000):
            self.store._get_lock(str(index))
        self.store.revoke("target")
        self.assertIs(self.store._get_lock("target"), lock)
        self.assertEqual(len(self.store._locks), 256)
        await entered.wait()
        self.assertTrue(lock.locked())
        finish.set()
        await task
        self.assertFalse(lock.locked())

    async def test_cancelled_waiter_does_not_prevent_later_acquisition(self):
        lock = self.store._get_lock("target")
        await lock.acquire()
        waiter = asyncio.create_task(lock.acquire())
        await asyncio.sleep(0)
        waiter.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await waiter
        lock.release()
        async with self.store._get_lock("target"):
            self.assertTrue(lock.locked())

    @patch("src.settings.oauth_session.verify_auth_token")
    async def test_expiring_access_token_is_refreshed_and_rotated(self, verify_token):
        now = int(time.time())
        verify_token.side_effect = [
            verified_claims(now + 1),
            verified_claims(now + 3600),
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
            verified_claims(now + 1),
            verified_claims(now + 3600),
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
        verify_token.return_value = verified_claims(now + 60)
        session_id = self.store.create({"access_token": "old-access", "refresh_token": "old-refresh"})
        self.oauth.refresh.side_effect = OAuthSessionUnavailable("temporary outage")

        resolved = await self.store.resolve(session_id)

        self.assertEqual(resolved.access_token, "old-access")

    @patch("src.settings.oauth_session.verify_auth_token")
    async def test_rejected_refresh_revokes_local_session(self, verify_token):
        now = int(time.time())
        verify_token.return_value = verified_claims(now + 1)
        session_id = self.store.create({"access_token": "old-access", "refresh_token": "old-refresh"})
        self.oauth.refresh.side_effect = OAuthSessionExpired("revoked")

        with self.assertRaises(OAuthSessionExpired):
            await self.store.resolve(session_id)
        with self.assertRaises(OAuthSessionExpired):
            await self.store.resolve(session_id)

    @patch("src.settings.oauth_session.verify_auth_token")
    async def test_logout_revokes_refresh_token_before_local_session(self, verify_token):
        now = int(time.time())
        verify_token.return_value = verified_claims(now + 3600)
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
        verify_token.return_value = verified_claims(now + 3600)
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
            verified_claims(now + 3600),
            verified_claims(now + 3600),
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
