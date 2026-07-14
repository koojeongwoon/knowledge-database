import json
import logging
import threading
import time
from datetime import datetime, timezone

import redis  # redis.exceptions.ResponseError 참조를 위해 유지
from src.core.cache.factory import StreamRedisClient, WikiCacheManager
from src.core.database.factory import DatabaseManager

logger = logging.getLogger("event_consumer")

def _parse_expires_at(dt_str: str) -> datetime:
    """Java에서 전송된 ISO 날짜 포맷(나노초 포함 가능)을 파이썬 datetime(UTC)으로 안전하게 변환합니다."""
    # 소수점 이하 나노초 단위가 기입되어 있는 경우 파이썬 파싱을 위해 마이크로초 6자리로 자름
    if "." in dt_str:
        base, frac = dt_str.split(".", 1)
        frac = frac[:6]
        dt_str = f"{base}.{frac}"
        
    if dt_str.endswith("Z"):
        dt_str = dt_str[:-1]
        return datetime.fromisoformat(dt_str).replace(tzinfo=timezone.utc)
        
    return datetime.fromisoformat(dt_str).replace(tzinfo=timezone.utc)

class UserSignupEventConsumer(threading.Thread):
    def __init__(self):
        super().__init__()
        self.daemon = True
        self.stream_key_signup = "auth:stream:user-signup"
        self.stream_key_apikey = "auth:stream:api-key-change"
        self.group_name = "llm-wiki-group"
        self.consumer_name = "llm-wiki-consumer-1"
        self.running = True
        
        # Redis Stream 클라이언트 (공통 팩토리로 생성 — 연결 파라미터는 config.py에서 중앙 관리)
        self.redis_client = StreamRedisClient()
        
        # 지식베이스 전용 캐시 매니저 (이벤트 수신 시 즉각 캐시 무효화/적재를 위함)
        self.cache_manager = WikiCacheManager()

    def run(self):
        logger.info("Starting UserSignupEventConsumer thread...")
        
        # 1. 두 스트림에 대한 소비자 그룹 각각 생성
        for stream_key in (self.stream_key_signup, self.stream_key_apikey):
            try:
                self.redis_client.xgroup_create(stream_key, self.group_name, id="0", mkstream=True)
                logger.info(f"Consumer group {self.group_name} created for stream {stream_key}")
            except redis.exceptions.ResponseError as e:
                if "BUSYGROUP" in str(e):
                    logger.info(f"Consumer group {self.group_name} already exists for stream {stream_key}.")
                else:
                    logger.error(f"Failed to create consumer group for {stream_key}: {e}")
                
        # 2. 루프를 돌며 두 스트림 멀티플렉싱(Multiplexing) Consume
        while self.running:
            try:
                # 2.1 Pending 상태인 메시지들 먼저 복구 처리
                pending_streams = self.redis_client.xreadgroup(
                    groupname=self.group_name,
                    consumername=self.consumer_name,
                    streams={self.stream_key_signup: "0", self.stream_key_apikey: "0"},
                    count=10,
                    block=1000
                )
                
                has_pending = False
                if pending_streams:
                    for stream_name, messages in pending_streams:
                        if messages:
                            has_pending = True
                            break

                if has_pending:
                    logger.info("Processing pending outbox events...")
                    self._process_streams(pending_streams)

                # 2.2 새로운 메시지 Consume (>)
                streams = self.redis_client.xreadgroup(
                    groupname=self.group_name,
                    consumername=self.consumer_name,
                    streams={self.stream_key_signup: ">", self.stream_key_apikey: ">"},
                    count=10,
                    block=2000
                )
                
                has_new = False
                if streams:
                    for stream_name, messages in streams:
                        if messages:
                            has_new = True
                            break

                if has_new:
                    self._process_streams(streams)
                        
            except Exception as e:
                logger.error(f"Error in EventConsumer loop: {e}")
                time.sleep(5)  # 에러 발생 시 대기 후 재시도

    def _process_streams(self, streams):
        for stream_name, messages in streams:
            for message_id, message_data in messages:
                payload_str = message_data.get("payload")
                if not payload_str:
                    self.redis_client.xack(stream_name, self.group_name, message_id)
                    continue
                    
                logger.info(f"Received event from {stream_name} (id: {message_id}): {payload_str}")
                try:
                    payload = json.loads(payload_str)
                    
                    if stream_name == self.stream_key_signup:
                        auth_id = payload.get("authId")
                        if auth_id:
                            self._upsert_user(auth_id)
                    elif stream_name == self.stream_key_apikey:
                        self._process_api_key(payload)
                    
                    # 성공적으로 DB 복제 완료 후 Ack 전송
                    self.redis_client.xack(stream_name, self.group_name, message_id)
                except Exception as ex:
                    logger.error(f"Failed to process message {message_id} in stream {stream_name}: {ex}")

    def _upsert_user(self, sub_val: str):
        import uuid
        user_id = str(uuid.uuid4())
        try:
            with DatabaseManager().cursor() as cur:
                cur.execute("""
                    INSERT INTO knowledge_users (user_id, sub_val)
                    VALUES (%s, %s)
                    ON CONFLICT (sub_val) DO NOTHING;
                """, (user_id, sub_val))
            logger.info(f"Successfully mapped sub_val ({sub_val}) to internal user_id ({user_id}) in knowledge_db")
        except Exception as e:
            logger.error(f"Failed to replicate user UUID to database: {e}")
            raise e

    def _process_api_key(self, payload: dict):
        action = payload.get("action")
        key_hash = payload.get("keyHash")
        
        if not key_hash:
            return
            
        cache_key = f"auth:token:hash:{key_hash}"
            
        if action == "CREATED":
            auth_id = payload.get("authId")
            key_name = payload.get("keyName")
            expires_at = payload.get("expiresAt")
            
            if not auth_id:
                return
                
            # 1. sub_val (auth_id)를 기준으로 내부 user_id 조회
            user_id = self._get_user_id_by_sub(auth_id)
            if not user_id:
                # 동시성 방어 코드: 회원 정보가 유입되기 전에 키가 먼저 올 경우, 유저를 임시 선 등록
                import uuid
                user_id = str(uuid.uuid4())
                try:
                    with DatabaseManager().cursor() as cur:
                        cur.execute("""
                            INSERT INTO knowledge_users (user_id, sub_val)
                            VALUES (%s, %s)
                            ON CONFLICT (sub_val) DO NOTHING;
                        """, (user_id, auth_id))
                    logger.info(f"Proactively created user_id {user_id} for sub_val {auth_id} upon API Key replication")
                except Exception as e:
                    logger.error(f"Proactive user creation failed: {e}")
                    raise e
            
            # 2. api_keys 복제 적재
            try:
                with DatabaseManager().cursor() as cur:
                    cur.execute("""
                        INSERT INTO knowledge_api_keys (api_key_hash, user_id, key_name, expires_at)
                        VALUES (%s, %s, %s, %s)
                        ON CONFLICT (api_key_hash) DO UPDATE SET
                            user_id = EXCLUDED.user_id,
                            key_name = EXCLUDED.key_name,
                            expires_at = EXCLUDED.expires_at;
                    """, (key_hash, user_id, key_name, expires_at))
                logger.info(f"Successfully replicated API Key ({key_name}) for internal user {user_id}")
                
                # 3. 0ms 캐시 히트를 위해 지식베이스 전용 Redis 캐시에도 실시간 Warm-up 적재 (만료시간 고려 정밀 TTL 적용)
                if expires_at:
                    now_utc = datetime.now(timezone.utc)
                    expires_dt = _parse_expires_at(expires_at)
                    remaining_seconds = int((expires_dt - now_utc).total_seconds())
                    
                    if remaining_seconds > 0:
                        cache_data = {
                            "valid": True, 
                            "api_key": "HIDDEN", 
                            "user_id": user_id,
                            "expires_at": expires_dt.isoformat()
                        }
                        # 최대 24시간 보관 (Redis 메모리 폭증 방지) 또는 실제 남은 만료시간 중 최소값으로 TTL 지정
                        ttl = min(86400, remaining_seconds)
                        self.cache_manager.set(cache_key, json.dumps(cache_data), ttl=ttl)
                        logger.info(f"Successfully warmed up API Key cache with TTL {ttl}s for hash: {key_hash}")
                    else:
                        logger.warning(f"Replicated API Key {key_name} is already expired at {expires_at}")
            except Exception as e:
                logger.error(f"Failed to insert API Key to database: {e}")
                raise e
                
        elif action == "REVOKED":
            try:
                with DatabaseManager().cursor() as cur:
                    cur.execute("DELETE FROM knowledge_api_keys WHERE api_key_hash = %s;", (key_hash,))
                logger.info(f"Successfully deleted API Key hash ({key_hash}) from database")
                
                # 3. 보안을 즉시 회수하기 위해 지식베이스 전용 Redis 캐시에서도 즉각 영구 삭제 (Instant Revoke)
                self.cache_manager.delete(cache_key)
                logger.info(f"Successfully evicted API Key cache for hash: {key_hash}")
            except Exception as e:
                logger.error(f"Failed to delete API Key from database: {e}")
                raise e

    def _get_user_id_by_sub(self, sub_val: str) -> str:
        try:
            with DatabaseManager().cursor() as cur:
                cur.execute("SELECT user_id FROM knowledge_users WHERE sub_val = %s;", (sub_val,))
                row = cur.fetchone()
                return row[0] if row else None
        except Exception as e:
            logger.error(f"Database fetch user_id failed: {e}")
            return None

    def stop(self):
        self.running = False
