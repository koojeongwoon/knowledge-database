import datetime
import json
import logging
import queue
from logging.handlers import QueueHandler, QueueListener
from typing import Any, Dict

# 1. 커스텀 데이터베이스 감사 로깅 핸들러 정의
class PostgresAuditHandler(logging.Handler):
    def emit(self, record):
        try:
            msg = record.getMessage()
            if msg.startswith("[AUDIT] "):
                json_str = msg[len("[AUDIT] "):]
                audit_data = json.loads(json_str)
                
                from src.core.database.factory import DatabaseManager
                # cursor() 컨텍스트 매니저를 사용하여 자원 반납 보장 및 누수 방지
                with DatabaseManager().cursor() as cur:
                    cur.execute("""
                        INSERT INTO knowledge_audit_logs (user_id, action, status, payload)
                        VALUES (%s, %s, %s, %s);
                    """, (
                        audit_data["user_id"],
                        audit_data["action"],
                        audit_data["status"],
                        json.dumps(audit_data["payload"], ensure_ascii=False)
                    ))
        except Exception as e:
            # 감사 로깅 중 데이터베이스 에러 등이 나더라도
            # 자체 로깅 에러 처리를 수행 (sys.stderr 등에 남김)
            self.handleError(record)

# 2. 로거 생성 및 설정
logger = logging.getLogger("audit")
logger.setLevel(logging.INFO)
# 기존 핸들러 제거 (중복 등록 방지)
logger.handlers.clear()

# 3. 비동기 큐 핸들러 및 리스너 대상 핸들러 생성
# (1) 표준 출력용 스트림 핸들러
stream_handler = logging.StreamHandler()
stream_handler.setLevel(logging.INFO)

# 포맷터 설정
formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
stream_handler.setFormatter(formatter)

# (2) PostgreSQL DB 저장 핸들러
db_handler = PostgresAuditHandler()
db_handler.setLevel(logging.INFO)

# 4. 비동기 Queue 및 Listener 세팅
log_queue = queue.Queue(-1) # 무제한 큐
queue_handler = QueueHandler(log_queue)
logger.addHandler(queue_handler)

# 백그라운드 리스너 기동 (stdout/DB 기록)
listener = QueueListener(log_queue, stream_handler, db_handler, respect_handler_level=True)
listener.start()

# 어플리케이션 종료 시 리스너를 정지하기 위한 등록 (선택적이나 안전장치)
import atexit
atexit.register(listener.stop)


def log_audit(action: str, status: str, user_id: str = "SYSTEM", payload: Dict[str, Any] = None):
    """
    구조화된 JSON 감사 로그를 stdout과 PostgreSQL에 남깁니다.
    """
    audit_data = {
        "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "level": "AUDIT",
        "user_id": user_id[:16] if user_id else "SYSTEM",  # API Key 노출 방지(식별 토큰만 수집)
        "action": action,
        "status": status,
        "payload": payload or {}
    }
    
    # 큐 핸들러를 타게 하여 비동기 처리 유도
    logger.info(f"[AUDIT] {json.dumps(audit_data, ensure_ascii=False)}")
