import atexit
import datetime
import json
import logging
import os
import queue
from logging.handlers import QueueHandler, QueueListener, TimedRotatingFileHandler
from pathlib import Path
from typing import Any, Dict

from src.core.security.sanitizer import sanitize_dict

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
        except Exception:
            # DB 미연결 또는 일시적 오류 시 비동기 큐 리스너 종료 및 로깅 방해 없이 무음 스킵
            pass


# 2. 로그 디렉토리 및 파일 경로 보장
LOG_DIR = Path("logs")
LOG_DIR.mkdir(parents=True, exist_ok=True)
AUDIT_LOG_FILE = LOG_DIR / "audit.log"

# 3. 로거 생성 및 초기화
logger = logging.getLogger("audit")
logger.setLevel(logging.INFO)
logger.handlers.clear()

# 포맷터 설정
formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')

# (1) 표준 출력용 스트림 핸들러
stream_handler = logging.StreamHandler()
stream_handler.setLevel(logging.INFO)
stream_handler.setFormatter(formatter)

# (2) 비동기 일자별 롤링 파일 핸들러 (TimedRotatingFileHandler)
# 매일 자정(midnight) 롤링, 최대 30일 보관, UTF-8 인코딩
file_handler = TimedRotatingFileHandler(
    filename=str(AUDIT_LOG_FILE),
    when="midnight",
    interval=1,
    backupCount=30,
    encoding="utf-8"
)
file_handler.setLevel(logging.INFO)
file_handler.setFormatter(formatter)

# (3) PostgreSQL DB 저장 핸들러
db_handler = PostgresAuditHandler()
db_handler.setLevel(logging.INFO)

# 4. 비동기 Queue 및 Listener 세팅
log_queue = queue.Queue(-1)  # 무제한 큐
queue_handler = QueueHandler(log_queue)
logger.addHandler(queue_handler)

# 백그라운드 리스너 기동 (스트림, 파일 롤링, DB 기록을 독립 스레드에서 비동기 처리)
listener = QueueListener(
    log_queue,
    stream_handler,
    file_handler,
    db_handler,
    respect_handler_level=True
)
listener.start()

# 애플리케이션 종료 시 리스너 안전 정지
atexit.register(listener.stop)


def log_audit(action: str, status: str, user_id: str = "SYSTEM", payload: Dict[str, Any] = None):
    """
    구조화된 JSON 감사 로그를 PII 마스킹 후 stdout, 롤링 파일, PostgreSQL에 비동기로 안전하게 남깁니다.
    """
    safe_payload = sanitize_dict(payload or {})
    audit_data = {
        "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "level": "AUDIT",
        "user_id": user_id[:16] if user_id else "SYSTEM",  # API Key 노출 방지
        "action": action,
        "status": status,
        "payload": safe_payload
    }
    
    # QueueHandler를 통해 큐에 즉시 삽입 (Non-blocking)
    logger.info(f"[AUDIT] {json.dumps(audit_data, ensure_ascii=False)}")
