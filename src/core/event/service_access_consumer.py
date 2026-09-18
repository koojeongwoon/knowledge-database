import logging
import threading
import time

import redis
from pydantic import ValidationError

from src.api_keys.auth import AUTH_TOKEN_ISSUER, KNOWLEDGE_CLIENT_ID, KNOWLEDGE_TENANT_ID
from src.core.cache.factory import AppCacheManager, StreamRedisClient, WikiCacheManager
from src.core.database.factory import DatabaseManager
from src.core.event.user_service_access import parse_iam_user_service_access_event


logger = logging.getLogger("iam_service_access_consumer")
SERVICE_ACCESS_STREAM = "iam:events:user-service-access:v1"
SERVICE_ACCESS_GROUP = "knowledge-service-access-v1"
SERVICE_ACCESS_DLQ = f"{SERVICE_ACCESS_STREAM}:knowledge:dlq"
MAX_ATTEMPTS = 5


def apply_service_access_event(event, db_manager=None) -> tuple[bool, str | None, list[str]]:
    if event.issuer != AUTH_TOKEN_ISSUER or event.tenant_id != KNOWLEDGE_TENANT_ID:
        # Ignore events for other tenants without error (ACKed with no effect)
        return False, None, []
    if event.client_id != KNOWLEDGE_CLIENT_ID:
        # Ignore events for other clients without error (ACKed with no effect)
        return False, None, []

    manager = db_manager or DatabaseManager()
    user_id = None
    key_hashes = []
    with manager.transaction() as cur:
        cur.execute(
            """
            INSERT INTO iam_user_service_access_events(event_id, tenant_id, subject_id, client_id, access_version)
            VALUES (%s, %s, %s, %s, %s) ON CONFLICT DO NOTHING RETURNING event_id
            """,
            (event.event_id, event.tenant_id, event.subject_id, event.client_id, event.access_version),
        )
        if not cur.fetchone():
            return False, None, []

        cur.execute(
            """
            INSERT INTO iam_user_service_access_states
                (tenant_id, subject_id, client_id, service_access_status, access_version, last_event_id)
            VALUES (%s, %s, %s, %s, %s, %s)
            ON CONFLICT (tenant_id, subject_id, client_id) DO UPDATE SET
                service_access_status = EXCLUDED.service_access_status,
                access_version = EXCLUDED.access_version,
                last_event_id = EXCLUDED.last_event_id,
                updated_at = CURRENT_TIMESTAMP
            WHERE iam_user_service_access_states.access_version < EXCLUDED.access_version
              AND iam_user_service_access_states.service_access_status <> 'WITHDRAWN'
            RETURNING subject_id
            """,
            (event.tenant_id, event.subject_id, event.client_id, event.status, event.access_version, event.event_id),
        )
        if not cur.fetchone():
            return False, None, []

        if event.status in {"DISABLED", "WITHDRAWN"}:
            cur.execute(
                "SELECT user_id FROM knowledge_users WHERE tenant_id = %s AND sub_val = %s",
                (event.tenant_id, event.subject_id),
            )
            row = cur.fetchone()
            if row:
                user_id = row[0]
                cur.execute("UPDATE knowledge_api_keys SET is_active = FALSE WHERE user_id = %s", (user_id,))
                cur.execute("SELECT api_key_hash FROM knowledge_api_keys WHERE user_id = %s", (user_id,))
                key_hashes = [r[0] for r in cur.fetchall()]

    return True, user_id, key_hashes


class IamServiceAccessConsumer(threading.Thread):
    def __init__(self, stream_client=None, db_manager_factory=DatabaseManager):
        super().__init__(daemon=True)
        self.redis_client = stream_client or StreamRedisClient()
        self.db_manager_factory = db_manager_factory
        self.consumer_name = f"knowledge-service-access-{id(self)}"
        self.running = True

    def run(self):
        try:
            self.redis_client.xgroup_create(SERVICE_ACCESS_STREAM, SERVICE_ACCESS_GROUP, id="0", mkstream=True)
        except redis.exceptions.ResponseError as error:
            if "BUSYGROUP" not in str(error):
                raise
        while self.running:
            try:
                claimed = self.redis_client.xautoclaim(
                    SERVICE_ACCESS_STREAM, SERVICE_ACCESS_GROUP, self.consumer_name, min_idle_time=60000,
                    start_id="0-0", count=10,
                )
                if len(claimed) > 1 and claimed[1]:
                    self._process_streams([(SERVICE_ACCESS_STREAM, claimed[1])])
                streams = self.redis_client.xreadgroup(
                    groupname=SERVICE_ACCESS_GROUP, consumername=self.consumer_name,
                    streams={SERVICE_ACCESS_STREAM: ">"}, count=10, block=2000,
                )
                with self.db_manager_factory().transaction() as cur:
                    cur.execute(
                        "UPDATE iam_user_service_access_health SET last_seen_at = CURRENT_TIMESTAMP WHERE singleton"
                    )
                self._process_streams(streams or [])
            except Exception as error:
                if self.running:
                    logger.error("Service access consumer poll failed: %s", type(error).__name__)
                    time.sleep(1)

    def _process_streams(self, streams):
        for _, messages in streams:
            for message_id, fields in messages:
                self._process(message_id, fields.get("data"))

    def _process(self, message_id, data):
        retry_key = f"{SERVICE_ACCESS_GROUP}:retry:{message_id}"
        try:
            event = parse_iam_user_service_access_event(data or "null")
            applied, user_id, hashes = apply_service_access_event(event, self.db_manager_factory())
            if applied and user_id:
                wiki_cache = WikiCacheManager()
                for key_hash in hashes:
                    wiki_cache.delete(f"auth:token:hash:{key_hash}")
            if applied and event.status in {"DISABLED", "WITHDRAWN"}:
                app_cache = AppCacheManager()
                index = f"knowledge:web-user:{event.subject_id}"
                session_keys = list(app_cache.client.smembers(index))
                if session_keys:
                    app_cache.client.delete(*session_keys)
                app_cache.client.delete(index)
            self.redis_client.xack(SERVICE_ACCESS_STREAM, SERVICE_ACCESS_GROUP, message_id)
            self.redis_client.delete(retry_key)
        except (ValidationError, ValueError, TypeError):
            self.redis_client.xadd(SERVICE_ACCESS_DLQ, {"sourceId": message_id, "reason": "INVALID_EVENT"})
            self.redis_client.xack(SERVICE_ACCESS_STREAM, SERVICE_ACCESS_GROUP, message_id)
        except Exception:
            attempts = self.redis_client.incr(retry_key)
            self.redis_client.expire(retry_key, 86400)
            if attempts >= MAX_ATTEMPTS:
                self.redis_client.xadd(SERVICE_ACCESS_DLQ, {"sourceId": message_id, "reason": "PROCESSING_FAILED"})
                self.redis_client.xack(SERVICE_ACCESS_STREAM, SERVICE_ACCESS_GROUP, message_id)

    def stop(self):
        self.running = False


IamUserServiceAccessConsumer = IamServiceAccessConsumer
