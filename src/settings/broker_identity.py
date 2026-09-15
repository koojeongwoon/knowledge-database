"""Resolve a service-authenticated local owner to its persisted IAM subject."""
from src.core.database.factory import DatabaseManager


class BrokerIdentityRepository:
    def __init__(self, database_factory=DatabaseManager):
        self.database_factory = database_factory

    def subject_for_owner(self, owner_id):
        # Exact mapping only: no caller-selected sub, SYSTEM, or shared-owner fallback.
        if not owner_id or owner_id == 'SYSTEM':
            raise ValueError('Verified Knowledge owner required')
        with self.database_factory().cursor() as cur:
            cur.execute('SELECT sub_val FROM knowledge_users WHERE user_id=%s', (owner_id,))
            row = cur.fetchone()
        if row is None or not isinstance(row[0], str) or not row[0].strip():
            raise ValueError('Knowledge owner has no verified IAM mapping')
        return row[0]
