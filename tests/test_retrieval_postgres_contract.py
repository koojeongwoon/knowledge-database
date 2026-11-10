"""Real SQL contracts, opt-in only inside the disposable load fixture."""
import os
from pathlib import Path
import sys
import unittest
import uuid

if __name__ == "__main__":
    sys.path.insert(0, "/app" if Path("/app/src").exists() else str(Path(__file__).resolve().parents[1]))

from pgvector import Vector
from src.core.config import current_user_config, settings
from src.core.database.connection import PostgresDatabaseManager
from src.retrieval.infrastructure.retrieval_repository import PostgresRetrievalRepository


@unittest.skipUnless(os.getenv("KNOWLEDGE_RETRIEVAL_SQL_TEST") == "1", "disposable SQL fixture only")
class RetrievalPostgresContract(unittest.TestCase):
    def test_identity_read_stays_fresh_without_rewriting_the_user(self):
        from src.api_keys.auth import AUTH_TOKEN_ISSUER, KNOWLEDGE_TENANT_ID
        from src.api.exceptions import DatabaseException
        from src.users.service import LocalUserService, VerifiedUserIdentity
        self.assertTrue(settings.db_host.startswith('codex-db-load-'))
        self.assertEqual(settings.db_name,'knowledge_bench')
        database = PostgresDatabaseManager()
        subject = 'probe-'+uuid.uuid4().hex
        identity = VerifiedUserIdentity(AUTH_TOKEN_ISSUER, KNOWLEDGE_TENANT_ID, subject, 'probe@synthetic.invalid')
        users = LocalUserService(database)
        try:
            user_id = users.ensure_local_user(identity)
            with database.cursor() as cur:
                cur.execute('SELECT xmin::text FROM knowledge_users WHERE user_id=%s',(user_id,))
                original = cur.fetchone()[0]
            for _ in range(3):
                self.assertEqual(users.ensure_local_user(identity),user_id)
            with database.cursor() as cur:
                cur.execute('SELECT xmin::text FROM knowledge_users WHERE user_id=%s',(user_id,))
                self.assertEqual(cur.fetchone()[0],original)
                cur.execute("""INSERT INTO iam_user_lifecycle_states
                    (tenant_id,subject_id,lifecycle_status,user_version,last_event_id)
                    VALUES (%s,%s,'BLOCKED',2,%s)""",(KNOWLEDGE_TENANT_ID,subject,subject))
            with self.assertRaises(DatabaseException) as blocked:
                users.ensure_local_user(identity)
            self.assertIsInstance(blocked.exception.__cause__, ValueError)
        finally:
            with database.cursor() as cur:
                cur.execute('DELETE FROM iam_user_lifecycle_states WHERE tenant_id=%s AND subject_id=%s',(KNOWLEDGE_TENANT_ID,subject))
                cur.execute('DELETE FROM knowledge_users WHERE sub_val=%s',(subject,))
            database.close()

    def test_owner_filter_precedes_limit_and_baselines_stay_excluded(self):
        self.assertTrue(settings.db_host.startswith("codex-db-load-"))
        self.assertEqual(settings.db_name, "knowledge_bench")
        database = PostgresDatabaseManager()
        repository = PostgresRetrievalRepository(database)
        key = uuid.uuid4().hex
        owner, other = "probe-"+key, "other-"+key
        word = "contractprobe"+key[:8]
        public, private = f"qa/{key}-public.md", f"qa/{key}-private.md"
        vector = Vector([1.0]+[0.0]*1535)
        rows = [(public, owner, "public", word + " padding"*200),
                (private, owner, "private", word + " padding"*200),
                (f"baselines/{key}.md", owner, "private", word),
                (f"baseline-drafts/{key}.md", owner, "private", word)]
        rows.extend((f"qa/{key}-denied-{i}.md", other, "private", word+" "+word) for i in range(30))
        token = current_user_config.set({"user_id": owner})
        try:
            with database.cursor() as cur:
                cur.executemany("""INSERT INTO knowledge_documents
                    (file_path,owner_id,visibility,doc_type,title,content,parent_content,content_hash,embedding)
                    VALUES (%s,%s,%s,'topic',%s,%s,'','probe',%b)""",
                    [(path,who,visibility,"probe "+path,content,vector) for path,who,visibility,content in rows])
                cur.execute("""INSERT INTO knowledge_edges(source_path,target_topic,owner_id,visibility)
                    VALUES (%s,%s,%s,'public'),(%s,%s,%s,'public')""",
                    (public,"probe "+private,owner,public,"probe "+rows[-1][0],other))
            hits = repository.keyword_search(word, 2)
            self.assertEqual({d["file_path"] for d in hits}, {public, private})
            vector_hits = repository.similarity_search([1.0]+[0.0]*1535, 20)
            denied = {r[0] for r in rows[2:]}
            self.assertFalse(denied.intersection(d["file_path"] for d in vector_hits))
            graph = repository.get_connected_documents([public], 5)
            self.assertEqual({d["file_path"] for d in graph}, {private})
            current_user_config.set({"user_id": other})
            other_hits = repository.keyword_search(word, 50)
            self.assertNotIn(private, {d["file_path"] for d in other_hits})
            self.assertIn(public, {d["file_path"] for d in other_hits})
        finally:
            current_user_config.reset(token)
            with database.cursor() as cur:
                cur.execute("DELETE FROM knowledge_edges WHERE owner_id = ANY(%s)", ([owner,other],))
                cur.execute("DELETE FROM knowledge_documents WHERE owner_id = ANY(%s)", ([owner,other],))
            database.close()


if __name__ == "__main__":
    unittest.main()
