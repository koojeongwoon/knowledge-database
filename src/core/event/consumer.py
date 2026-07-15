import json
import logging
import threading
import time

import redis  # redis.exceptions.ResponseError 참조를 위해 유지
from src.core.cache.factory import StreamRedisClient
from src.core.database.factory import DatabaseManager

logger = logging.getLogger("event_consumer")

class UserSignupEventConsumer(threading.Thread):
    def __init__(self):
        super().__init__()
        self.daemon = True
        self.stream_key_signup = "auth:stream:user-signup"
        self.group_name = "llm-wiki-group"
        self.consumer_name = "llm-wiki-consumer-1"
        self.running = True
        
        # Redis Stream 클라이언트 (공통 팩토리로 생성 — 연결 파라미터는 config.py에서 중앙 관리)
        self.redis_client = StreamRedisClient()
        
    def run(self):
        logger.info("Starting UserSignupEventConsumer thread...")
        
        # 1. 두 스트림에 대한 소비자 그룹 각각 생성
        for stream_key in (self.stream_key_signup,):
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
                    streams={self.stream_key_signup: "0"},
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
                    streams={self.stream_key_signup: ">"},
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


    def stop(self):
        self.running = False
