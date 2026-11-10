"""Initialize the configured schema once per process, without caching user data."""
from threading import Lock

_ready = set()
_lock = Lock()


def ensure_database_schema(database) -> None:
    from src.core.database import migrations

    key = getattr(database, "schema_cache_key", None)
    if not isinstance(key, str):
        migrations.run_database_migrations(database)
        return
    with _lock:
        if key not in _ready:
            migrations.run_database_migrations(database)
            _ready.add(key)
