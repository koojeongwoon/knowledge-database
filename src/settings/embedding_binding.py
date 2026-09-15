"""Knowledge stores only the Broker delegation reference, never a provider credential."""
from contextlib import contextmanager

from src.core.database.factory import DatabaseManager


class BindingBusyError(RuntimeError):
    pass


class EmbeddingBindingRepository:
    def __init__(self, db_manager=None):
        # A fresh manager per operation avoids sharing a leased connection across indexing threads.
        self.db_factory = (lambda: db_manager) if db_manager is not None else DatabaseManager

    @contextmanager
    def lock(self, owner_id):
        with self.db_factory().transaction() as cur:
            cur.execute("SELECT pg_try_advisory_xact_lock(hashtextextended(%s, 0))",
                        ('knowledge-embedding-binding:' + owner_id,))
            if not cur.fetchone()[0]:
                raise BindingBusyError('Embedding binding is being changed')
            yield

    def get(self, owner_id):
        with self.db_factory().cursor() as cur:
            cur.execute("""
                SELECT grant_id, connection_id, credential_version, expires_at
                FROM knowledge_embedding_bindings WHERE owner_id=%s
            """, (owner_id,))
            row = cur.fetchone()
        if row is None:
            return None
        return dict(grant_id=str(row[0]),connection_id=str(row[1]),
                    credential_version=row[2],expires_at=row[3])

    def save(self, owner_id, binding):
        with self.db_factory().cursor() as cur:
            cur.execute("""
                INSERT INTO knowledge_embedding_bindings
                (owner_id,grant_id,connection_id,credential_version,expires_at)
                VALUES (%s,%s,%s,%s,%s)
                ON CONFLICT (owner_id) DO UPDATE SET grant_id=EXCLUDED.grant_id,
                    connection_id=EXCLUDED.connection_id,credential_version=EXCLUDED.credential_version,
                    expires_at=EXCLUDED.expires_at,updated_at=CURRENT_TIMESTAMP
            """, (owner_id,binding['id'],binding['connection_id'],
                  binding['credential_version'],binding['expires_at']))

    def delete(self, owner_id):
        with self.db_factory().cursor() as cur:
            cur.execute('DELETE FROM knowledge_embedding_bindings WHERE owner_id=%s', (owner_id,))
