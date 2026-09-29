import unittest
from concurrent.futures import ThreadPoolExecutor
from threading import Event
from unittest.mock import Mock, patch

from src.core.config import current_user_config
from src.core.storage import factory


class StorageCacheTests(unittest.TestCase):
    def setUp(self):
        factory._storage_instances.clear()

    def tearDown(self):
        factory._storage_instances.clear()

    def get_storage(self, owner, bucket="bucket"):
        token = current_user_config.set({
            "user_id": owner,
            "storage": {
                "storage_type": "s3",
                "s3_endpoint_url": "https://example.invalid",
                "s3_access_key_id": "test-key",
                "s3_secret_access_key": "test-secret",
                "s3_bucket_name": bucket,
            },
        })
        try:
            return factory.StorageManager()
        finally:
            current_user_config.reset(token)

    @patch("src.core.storage.s3.S3StorageManager", side_effect=lambda **kwargs: Mock())
    def test_evicts_least_recently_used_without_closing_active_caller(self, manager):
        first = self.get_storage("0")
        for index in range(1, 32):
            self.get_storage(str(index))
        self.assertIs(self.get_storage("0"), first)
        self.get_storage("32")
        self.assertEqual(len(factory._storage_instances), 32)
        self.assertIn("0", factory._storage_instances)
        self.assertNotIn("1", factory._storage_instances)
        first.close.assert_not_called()

    @patch("src.core.storage.s3.S3StorageManager", side_effect=lambda **kwargs: Mock())
    def test_changed_settings_and_invalidation_replace_cached_manager(self, manager):
        first = self.get_storage("owner")
        second = self.get_storage("owner", "new-bucket")
        self.assertIsNot(first, second)
        factory.invalidate_storage_cache("owner")
        self.assertIsNot(second, self.get_storage("owner", "new-bucket"))
        self.assertEqual(len(factory._storage_instances), 1)

    @patch("src.core.storage.s3.S3StorageManager", side_effect=lambda **kwargs: Mock())
    def test_concurrent_callers_reuse_same_manager_and_keep_cache_bounded(self, manager):
        with ThreadPoolExecutor(max_workers=8) as executor:
            same_owner = list(executor.map(self.get_storage, ["same"] * 40))
            list(executor.map(self.get_storage, map(str, range(100))))
        self.assertTrue(all(item is same_owner[0] for item in same_owner))
        self.assertEqual(manager.call_count, 101)
        self.assertEqual(len(factory._storage_instances), 32)

    def test_slow_construction_does_not_block_another_owners_cache_hit(self):
        started, release = Event(), Event()
        cached = Mock()
        slow_owner = next(
            str(index) for index in range(10000)
            if factory._creation_lock(str(index)) is factory._creation_lock("cached-owner")
        )

        def construct(**kwargs):
            if kwargs["bucket_name"] == "slow":
                started.set()
                if not release.wait(5):
                    raise TimeoutError("test construction was not released")
            return cached if kwargs["bucket_name"] == "cached" else Mock()

        with patch("src.core.storage.s3.S3StorageManager", side_effect=construct):
            self.get_storage("cached-owner", "cached")
            with ThreadPoolExecutor(max_workers=2) as executor:
                slow = executor.submit(self.get_storage, slow_owner, "slow")
                try:
                    self.assertTrue(started.wait(2))
                    hit = executor.submit(self.get_storage, "cached-owner", "cached")
                    self.assertIs(hit.result(timeout=2), cached)
                finally:
                    release.set()
                slow.result(timeout=2)

    def test_different_creation_stripes_can_construct_concurrently(self):
        first_owner = "first"
        second_owner = next(
            str(index) for index in range(1000)
            if factory._creation_lock(str(index)) is not factory._creation_lock(first_owner)
        )
        started, second_started, release = Event(), Event(), Event()

        def construct(**kwargs):
            if kwargs["bucket_name"] == "slow":
                started.set()
                if not release.wait(5):
                    raise TimeoutError("test construction was not released")
            else:
                second_started.set()
            return Mock()

        with patch("src.core.storage.s3.S3StorageManager", side_effect=construct):
            with ThreadPoolExecutor(max_workers=2) as executor:
                first = executor.submit(self.get_storage, first_owner, "slow")
                try:
                    self.assertTrue(started.wait(2))
                    second = executor.submit(self.get_storage, second_owner)
                    self.assertTrue(second_started.wait(2))
                    second.result(timeout=2)
                finally:
                    release.set()
                first.result(timeout=2)

    def test_invalidation_removes_client_created_while_settings_change(self):
        started, invalidating, release = Event(), Event(), Event()

        def construct(**kwargs):
            started.set()
            if not release.wait(5):
                raise TimeoutError("test construction was not released")
            return Mock()

        def invalidate():
            invalidating.set()
            factory.invalidate_storage_cache("owner")

        with patch("src.core.storage.s3.S3StorageManager", side_effect=construct):
            with ThreadPoolExecutor(max_workers=2) as executor:
                creating = executor.submit(self.get_storage, "owner")
                try:
                    self.assertTrue(started.wait(2))
                    invalidation = executor.submit(invalidate)
                    self.assertTrue(invalidating.wait(2))
                    self.assertFalse(invalidation.done())
                finally:
                    release.set()
                creating.result(timeout=2)
                invalidation.result(timeout=2)
        self.assertNotIn("owner", factory._storage_instances)

    @patch("src.core.storage.s3.S3StorageManager")
    def test_construction_failure_releases_lock_for_retry(self, manager):
        manager.side_effect = [RuntimeError("construction failed"), Mock()]
        with self.assertRaises(RuntimeError):
            self.get_storage("owner")
        self.assertNotIn("owner", factory._storage_instances)
        self.assertIs(self.get_storage("owner"), factory._storage_instances["owner"][1])
