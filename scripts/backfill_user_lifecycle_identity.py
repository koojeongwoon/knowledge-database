"""Backfill Knowledge user identity from IAM without printing personal identifiers."""

import argparse
import os

import psycopg
from psycopg import sql


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true", help="commit verified matches and quarantine safe orphans")
    args = parser.parse_args()
    iam_dsn = os.environ["IAM_DATABASE_DSN"]
    knowledge_dsn = os.environ["KNOWLEDGE_DATABASE_DSN"]
    tenant_id = os.environ["KNOWLEDGE_TENANT_ID"]

    with psycopg.connect(iam_dsn) as iam, psycopg.connect(knowledge_dsn) as knowledge:
        with iam.cursor() as cursor:
            cursor.execute("SELECT auth_id, email, name, status, lifecycle_version FROM users WHERE tenant_id = %s", (tenant_id,))
            authoritative = {row[0]: row[1:] for row in cursor.fetchall()}
        with knowledge.cursor() as cursor:
            cursor.execute("SELECT user_id, sub_val FROM knowledge_users")
            local = cursor.fetchall()
            matched = [(user_id, subject, authoritative[subject]) for user_id, subject in local if subject in authoritative]
            orphans = [(user_id, subject) for user_id, subject in local if subject not in authoritative]
            referenced_orphans = referenced_user_count(cursor, [user_id for user_id, _ in orphans])
            print(f"matched={len(matched)} orphaned={len(orphans)} referenced_orphans={referenced_orphans}")
            if not args.apply:
                knowledge.rollback()
                return
            if referenced_orphans:
                raise RuntimeError("orphaned Knowledge users still own data; refusing automatic quarantine")
            for user_id, _subject, (email, name, status, version) in matched:
                cursor.execute(
                    """
                    UPDATE knowledge_users
                    SET tenant_id = %s, email = %s, name = %s,
                        lifecycle_status = %s, user_version = %s
                    WHERE user_id = %s AND tenant_id IS NULL
                    """,
                    (tenant_id, email, name, status, version, user_id),
                )
            if orphans:
                cursor.execute(
                    """
                    UPDATE knowledge_users SET lifecycle_status = 'WITHDRAWN'
                    WHERE user_id = ANY(%s) AND tenant_id IS NULL
                    """,
                    ([user_id for user_id, _ in orphans],),
                )
        knowledge.commit()
        print(f"applied_matched={len(matched)} quarantined_orphans={len(orphans)}")


def referenced_user_count(cursor, user_ids: list[str]) -> int:
    if not user_ids:
        return 0
    cursor.execute("""
        SELECT table_name, column_name
        FROM information_schema.columns
        WHERE table_schema = 'public'
          AND column_name IN ('owner_id', 'user_id')
          AND table_name <> 'knowledge_users'
    """)
    referenced: set[str] = set()
    for table_name, column_name in cursor.fetchall():
        query = sql.SQL("SELECT DISTINCT {column}::text FROM {table} WHERE {column} = ANY(%s)").format(
            column=sql.Identifier(column_name), table=sql.Identifier(table_name)
        )
        cursor.execute(query, (user_ids,))
        referenced.update(row[0] for row in cursor.fetchall())
    return len(referenced)


if __name__ == "__main__":
    main()
