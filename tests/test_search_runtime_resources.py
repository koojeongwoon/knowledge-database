from concurrent.futures import ThreadPoolExecutor
from unittest.mock import Mock

import pytest

from src.core.cache import factory
from src.core.database import schema, migrations


@pytest.mark.parametrize("name,constructor", [("WikiCacheManager", "RedisCacheManager"),
                                               ("AppCacheManager", "RedisCacheManager"),
                                               ("StreamRedisClient", "redis")])
def test_concurrent_cache_factory_calls_reuse_one_client(monkeypatch, name, constructor):
    monkeypatch.setattr(factory, "_clients", {})
    create = Mock(return_value=object())
    if constructor == "redis":
        monkeypatch.setattr(factory.redis, "Redis", create)
    else:
        monkeypatch.setattr(factory, constructor, create)
    with ThreadPoolExecutor(max_workers=8) as workers:
        results = list(workers.map(lambda _: getattr(factory, name)(), range(32)))
    assert all(result is results[0] for result in results)
    create.assert_called_once()


def test_delegated_identity_mapping_does_not_construct_unused_redis(monkeypatch):
    from src.api_keys import service
    create = Mock(side_effect=AssertionError("Unused cache was constructed"))
    monkeypatch.setattr(service, "WikiCacheManager", create)
    user = Mock()
    user.ensure_local_user.return_value = "owner"
    monkeypatch.setattr(service, "LocalUserService", lambda db: user)
    assert service.ApiKeyService(db_manager=object()).get_or_create_user(object()) == "owner"
    create.assert_not_called()


def test_api_key_revocation_still_evicts_the_shared_cache(monkeypatch):
    from contextlib import contextmanager
    from src.api_keys import service
    cache = Mock()
    create = Mock(return_value=cache)
    monkeypatch.setattr(service, "WikiCacheManager", create)
    cursor = Mock()
    cursor.fetchone.return_value = ("key-hash",)
    class Database:
        @contextmanager
        def transaction(self):
            yield cursor
    api = service.ApiKeyService(Database())
    monkeypatch.setattr(api, "get_or_create_user", lambda identity: "owner")
    assert api.revoke(object(), "key-id")
    cache.delete.assert_called_once_with("auth:token:hash:key-hash")


def test_schema_bootstrap_runs_once_for_concurrent_database_users(monkeypatch):
    monkeypatch.setattr(schema, "_ready", set())
    migrate = Mock()
    monkeypatch.setattr(migrations, "run_database_migrations", migrate)
    class Database:
        schema_cache_key = "database-one"
    with ThreadPoolExecutor(max_workers=8) as workers:
        list(workers.map(lambda _: schema.ensure_database_schema(Database()), range(32)))
    migrate.assert_called_once()
    database = Database()
    database.schema_cache_key = "database-two"
    schema.ensure_database_schema(database)
    assert migrate.call_count == 2


def test_failed_schema_bootstrap_is_retried(monkeypatch):
    monkeypatch.setattr(schema, "_ready", set())
    migrate = Mock(side_effect=[RuntimeError("unavailable"), None])
    monkeypatch.setattr(migrations, "run_database_migrations", migrate)
    class Database:
        schema_cache_key = "retry-database"
    with pytest.raises(RuntimeError, match="unavailable"):
        schema.ensure_database_schema(Database())
    schema.ensure_database_schema(Database())
    schema.ensure_database_schema(Database())
    assert migrate.call_count == 2
