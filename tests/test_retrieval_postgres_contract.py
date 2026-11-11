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
    def test_batched_search_writes_preserve_counts_owner_and_candidate_fields(self):
        from src.retrieval.feedback import SearchFeedbackService
        self.assertTrue(settings.db_host.startswith('codex-db-load-'))
        self.assertEqual(settings.db_name, 'knowledge_bench')
        database = PostgresDatabaseManager()
        owner, other = 'batch-'+uuid.uuid4().hex, 'other-'+uuid.uuid4().hex
        token = current_user_config.set({'user_id': owner})
        try:
            repository = PostgresRetrievalRepository(database)
            repository.increment_citation_count(['qa/b.md', 'qa/a.md', 'qa/a.md'])
            repository.increment_citation_count(['qa/a.md'])
            current_user_config.set({'user_id': other})
            repository.increment_citation_count(['qa/a.md'])
            with database.cursor() as cur:
                cur.execute('SELECT owner_id,file_path,citation_count FROM knowledge_citations '
                            'WHERE owner_id=ANY(%s) ORDER BY owner_id,file_path', ([owner,other],))
                self.assertEqual(set(cur.fetchall()), {(owner,'qa/a.md',3), (owner,'qa/b.md',1),
                                                      (other,'qa/a.md',1)})
            search_id = SearchFeedbackService(database).record_event(owner, 'synthetic query', [{
                'file_path':'qa/a.md','matched_chunk_index':2,'search_sources':['vector','keyword'],
                'vector_similarity':.8,'rrf_score':1.0,'reranker_score':None,
                'graph_context':[{'file_path':'qa/graph.md','retrieval_kind':'graph',
                                  'graph_sources':['qa/a.md'],'graph_target':'graph','graph_weight':.5}],
            }])
            with database.cursor() as cur:
                cur.execute('SELECT result_count,candidate_count FROM knowledge_search_events '
                            'WHERE search_id=%s AND owner_id=%s', (search_id,owner))
                self.assertEqual(cur.fetchone(), (2,2))
                cur.execute('SELECT file_path,chunk_index,retrieval_sources,retrieval_kind,'
                            'vector_score,reranker_score,final_rank,relation_path '
                            'FROM knowledge_search_candidates WHERE search_id=%s AND owner_id=%s '
                            'ORDER BY file_path', (search_id,owner))
                direct, graph = cur.fetchall()
                self.assertEqual(direct[:4], ('qa/a.md',2,['vector','keyword'],'direct'))
                self.assertAlmostEqual(direct[4], .8, places=5)
                self.assertEqual(direct[5:], (None,1,[]))
                self.assertEqual(graph[0], 'qa/graph.md')
                self.assertEqual(graph[3], 'graph')
                self.assertIsNone(graph[6])
                self.assertEqual(graph[7], [{'sources':['qa/a.md'],'target':'graph','weight':.5}])
        finally:
            current_user_config.reset(token)
            with database.cursor() as cur:
                cur.execute('DELETE FROM knowledge_search_events WHERE owner_id=ANY(%s)', ([owner,other],))
                cur.execute('DELETE FROM knowledge_citations WHERE owner_id=ANY(%s)', ([owner,other],))
            database.close()

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
