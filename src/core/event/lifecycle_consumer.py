import logging
import threading
import time

import redis
from pydantic import ValidationError

from src.api_keys.auth import AUTH_TOKEN_ISSUER, KNOWLEDGE_TENANT_ID
from src.core.cache.factory import AppCacheManager, StreamRedisClient, WikiCacheManager
from src.core.database.factory import DatabaseManager
from src.core.event.user_lifecycle import parse_iam_user_lifecycle_event


logger = logging.getLogger("iam_user_lifecycle_consumer")
STREAM = "iam:events:user:v1"
GROUP = "knowledge-user-lifecycle-v1"
DLQ = f"{STREAM}:knowledge:dlq"
MAX_ATTEMPTS = 5


def apply_user_lifecycle_event(event, db_manager=None) -> tuple[bool, str | None, list[str]]:
    if event.issuer != AUTH_TOKEN_ISSUER or event.tenant_id != KNOWLEDGE_TENANT_ID:
        raise ValueError("IAM lifecycle event is outside the Knowledge tenant boundary")
    manager = db_manager or DatabaseManager()
    user_id = None
    key_hashes = []
    with manager.transaction() as cur:
        cur.execute(
            """
            INSERT INTO iam_user_lifecycle_events(event_id, tenant_id, subject_id, user_version)
            VALUES (%s, %s, %s, %s) ON CONFLICT DO NOTHING RETURNING event_id
            """,
            (event.event_id, event.tenant_id, event.subject_id, event.user_version),
        )
        if not cur.fetchone():
            return False, None, []
        status = "BLOCKED" if event.event_type == "USER_DISABLED" else (
            "WITHDRAWN" if event.event_type == "USER_DELETED" else "ACTIVE"
        )
        cur.execute(
            """
            INSERT INTO iam_user_lifecycle_states
                (tenant_id, subject_id, lifecycle_status, user_version, last_event_id)
            VALUES (%s, %s, %s, %s, %s)
            ON CONFLICT (tenant_id, subject_id) DO UPDATE SET
                lifecycle_status = EXCLUDED.lifecycle_status,
                user_version = EXCLUDED.user_version,
                last_event_id = EXCLUDED.last_event_id,
                updated_at = CURRENT_TIMESTAMP
            WHERE iam_user_lifecycle_states.user_version < EXCLUDED.user_version
              AND iam_user_lifecycle_states.lifecycle_status <> 'WITHDRAWN'
            RETURNING subject_id
            """,
            (event.tenant_id, event.subject_id, status, event.user_version, event.event_id),
        )
        if not cur.fetchone():
            return False, None, []
        if event.event_type == "USER_CREATED":
            return True, None, []
        if event.event_type == "USER_UPDATED":
            cur.execute(
                """
                UPDATE knowledge_users SET email = %s, name = %s, user_version = %s
                WHERE tenant_id = %s AND sub_val = %s AND user_version < %s
                RETURNING user_id
                """,
                (event.profile.email, event.profile.name, event.user_version,
                 event.tenant_id, event.subject_id, event.user_version),
            )
        else:
            cur.execute(
                """
                UPDATE knowledge_users SET lifecycle_status = %s, user_version = %s
                WHERE tenant_id = %s AND sub_val = %s AND user_version < %s
                RETURNING user_id
                """,
                (status, event.user_version, event.tenant_id, event.subject_id, event.user_version),
            )
        row = cur.fetchone()
        user_id = row[0] if row else None
        if user_id:
            if event.event_type in {"USER_DISABLED", "USER_DELETED"}:
                cur.execute("UPDATE knowledge_api_keys SET is_active = FALSE WHERE user_id = %s", (user_id,))
            cur.execute("SELECT api_key_hash FROM knowledge_api_keys WHERE user_id = %s", (user_id,))
            key_hashes = [row[0] for row in cur.fetchall()]
    return True, user_id, key_hashes


class IamUserLifecycleConsumer(threading.Thread):
    def __init__(self, stream_client=None, db_manager_factory=DatabaseManager):
        super().__init__(daemon=True)
        self.redis_client = stream_client or StreamRedisClient()
        self.db_manager_factory = db_manager_factory
        self.consumer_name = f"knowledge-lifecycle-{id(self)}"
        self.running = True

    def run(self):
        try:
            self.redis_client.xgroup_create(STREAM, GROUP, id="0", mkstream=True)
        except redis.exceptions.ResponseError as error:
            if "BUSYGROUP" not in str(error):
                raise
        while self.running:
            try:
                claimed = self.redis_client.xautoclaim(
                    STREAM, GROUP, self.consumer_name, min_idle_time=60000,
                    start_id="0-0", count=10,
                )
                if len(claimed) > 1 and claimed[1]:
                    self._process_streams([(STREAM, claimed[1])])
                streams = self.redis_client.xreadgroup(
                    groupname=GROUP, consumername=self.consumer_name,
                    streams={STREAM: ">"}, count=10, block=2000,
                )
                with self.db_manager_factory().transaction() as cur:
                    cur.execute(
                        "UPDATE iam_user_lifecycle_health SET last_seen_at = CURRENT_TIMESTAMP WHERE singleton"
                    )
                self._process_streams(streams or [])
            except Exception as error:
                if self.running:
                    logger.error("Lifecycle consumer poll failed: %s", type(error).__name__)
                    time.sleep(1)

    def _process_streams(self, streams):
        for _, messages in streams:
            for message_id, fields in messages:
                self._process(message_id, fields.get("data"))

    def _process(self, message_id, data):
        retry_key = f"{GROUP}:retry:{message_id}"
        try:
            event = parse_iam_user_lifecycle_event(data or "null")
            applied, user_id, hashes = apply_user_lifecycle_event(event, self.db_manager_factory())
            if applied and user_id:
                wiki_cache = WikiCacheManager()
                for key_hash in hashes:
                    wiki_cache.delete(f"auth:token:hash:{key_hash}")
            if applied and event.event_type in {"USER_DISABLED", "USER_REENABLED", "USER_DELETED"}:
                app_cache = AppCacheManager()
                index = f"knowledge:web-user:{event.subject_id}"
                session_keys = list(app_cache.client.smembers(index))
                if session_keys:
                    app_cache.client.delete(*session_keys)
                app_cache.client.delete(index)
            self.redis_client.xack(STREAM, GROUP, message_id)
            self.redis_client.delete(retry_key)
        except (ValidationError, ValueError, TypeError) as error:
            self.redis_client.xadd(DLQ, {"sourceId": message_id, "reason": "INVALID_EVENT"})
            self.redis_client.xack(STREAM, GROUP, message_id)
        except Exception as error:
            attempts = self.redis_client.incr(retry_key)
            self.redis_client.expire(retry_key, 86400)
            if attempts >= MAX_ATTEMPTS:
                self.redis_client.xadd(DLQ, {"sourceId": message_id, "reason": "PROCESSING_FAILED"})
                self.redis_client.xack(STREAM, GROUP, message_id)

    def stop(self):
        self.running = False
