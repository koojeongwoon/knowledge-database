import datetime
import json
import logging
from typing import Any, Dict

logger = logging.getLogger("audit")

def log_audit(action: str, status: str, user_id: str = "SYSTEM", payload: Dict[str, Any] = None):
    """
    구조화된 JSON 감사 로그를 남기고 PostgreSQL 데이터베이스에 저장하는 공통 유틸리티 함수.
    [AUDIT] 접두사와 함께 JSON 스트링으로 출력하여 로그 수집기가 바로 긁어갈 수 있게 만듭니다.
    """
    audit_data = {
        "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "level": "AUDIT",
        "user_id": user_id[:16] if user_id else "SYSTEM",  # API Key 노출 방지(식별 토큰만 수집)
        "action": action,
        "status": status,
        "payload": payload or {}
    }
    
    # 1. 표준 출력 (로깅 파이프라인 연동용)
    logger.info(f"[AUDIT] {json.dumps(audit_data, ensure_ascii=False)}")
    
    # 2. 데이터베이스 저장
    try:
        from src.core.database.factory import DatabaseManager
        db_manager = DatabaseManager()
        db_manager.connect()
        with db_manager.conn.cursor() as cur:
            cur.execute("""
                INSERT INTO knowledge_audit_logs (user_id, action, status, payload)
                VALUES (%s, %s, %s, %s);
            """, (
                audit_data["user_id"],
                audit_data["action"],
                audit_data["status"],
                json.dumps(audit_data["payload"], ensure_ascii=False)
            ))
        db_manager.close()
    except Exception as e:
        # DB 저장 중 장애가 나더라도 서비스 비즈니스 로직에 영향을 주지 않도록 안전 가드
        logger.error(f"[AUDIT_DB_ERROR] Failed to save audit log to PostgreSQL: {e}")
