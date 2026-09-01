"""
knowledge DB에 저장된 기존 OpenAI/Codex OAuth Refresh Token을
IAM 중앙 인증서버(iam-server)의 codex_accounts 테이블로 이전하는 마이그레이션 스크립트.
"""

import os
import sys
import base64
import hashlib
from typing import Optional
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

try:
    import psycopg2
    from psycopg2.extras import RealDictCursor
except ImportError:
    psycopg2 = None


def get_iam_aes_key(secret_str: str) -> bytes:
    return hashlib.sha256(secret_str.encode("utf-8")).digest()


def encrypt_for_iam(plain_token: str, secret_key: bytes) -> str:
    aesgcm = AESGCM(secret_key)
    iv = os.urandom(12)
    cipher_text = aesgcm.encrypt(iv, plain_token.encode("utf-8"), None)
    combined = iv + cipher_text
    return base64.b64encode(combined).decode("utf-8")


def main():
    print("=== Codex OAuth Token Migration: Knowledge -> IAM Server ===")

    knowledge_db_url = os.getenv("KNOWLEDGE_DATABASE_URL", os.getenv("DATABASE_URL"))
    iam_db_url = os.getenv("IAM_DATABASE_URL", os.getenv("DATABASE_URL"))
    iam_secret = os.getenv("CODEX_ENCRYPTION_KEY", "iam-codex-secure-default-encryption-secret-key-32b")

    if not knowledge_db_url or not iam_db_url:
        print("Note: Set KNOWLEDGE_DATABASE_URL and IAM_DATABASE_URL environment variables if running in production.")
        return

    if not psycopg2:
        print("Error: psycopg2 is required. Run 'pip install psycopg2-binary' or 'pip install cryptography'.")
        return

    iam_key_bytes = get_iam_aes_key(iam_secret)

    # 1. Knowledge DB 조회
    conn_kb = psycopg2.connect(knowledge_db_url)
    cursor_kb = conn_kb.cursor(cursor_factory=RealDictCursor)

    query = """
        SELECT owner_id, openai_oauth_refresh_token_encrypted, openai_oauth_access_token_encrypted
        FROM user_settings
        WHERE openai_oauth_refresh_token_encrypted IS NOT NULL;
    """
    try:
        cursor_kb.execute(query)
        rows = cursor_kb.fetchall()
        print(f"Found {len(rows)} Codex linked account(s) in Knowledge DB.")
    except Exception as e:
        print(f"Failed to query knowledge DB: {e}")
        return
    finally:
        cursor_kb.close()
        conn_kb.close()

    if not rows:
        print("No tokens to migrate.")
        return

    # 2. IAM DB 삽입/업데이트
    conn_iam = psycopg2.connect(iam_db_url)
    cursor_iam = conn_iam.cursor()

    upsert_sql = """
        INSERT INTO codex_accounts (account_type, user_id, org_id, encrypted_refresh_token, scope, created_at, updated_at)
        VALUES (%s, %s, %s, %s, %s, NOW(), NOW())
        ON CONFLICT (user_id, account_type) 
        DO UPDATE SET encrypted_refresh_token = EXCLUDED.encrypted_refresh_token, updated_at = NOW();
    """

    migrated_count = 0
    for row in rows:
        owner_id = row["owner_id"]
        # 기존 knowledge의 암호화된 refresh_token 복호화 (필요시)
        # 만약 평문이거나 암호화 형식이 일치하지 않는 경우 복호화 후 IAM 방식으로 재암호화
        raw_refresh = row["openai_oauth_refresh_token_encrypted"]

        # IAM 방식으로 암호화하여 저장
        encrypted_for_iam = encrypt_for_iam(raw_refresh, iam_key_bytes)
        cursor_iam.execute(upsert_sql, ("USER", owner_id, None, encrypted_for_iam, "openid profile email model.request offline_access"))
        migrated_count += 1

    conn_iam.commit()
    cursor_iam.close()
    conn_iam.close()

    print(f"Successfully migrated {migrated_count} account(s) to IAM Server codex_accounts table!")


if __name__ == "__main__":
    main()
