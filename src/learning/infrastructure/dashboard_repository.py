from typing import Any, Dict, List

from src.learning.infrastructure.repository import _row


class LearningDashboardRepository:
    def __init__(self, db_manager):
        self.db_manager = db_manager

    def metrics(self, owner_id: str, days: int) -> Dict[str, Any]:
        with self.db_manager.cursor() as cur:
            cur.execute("""
                SELECT
                    COUNT(*) FILTER (WHERE status = 'active') AS active_sessions,
                    COUNT(*) FILTER (WHERE status = 'completed') AS completed_sessions,
                    COUNT(*) AS total_sessions
                FROM knowledge_learning_sessions WHERE owner_id = %s
            """, (owner_id,))
            sessions = _row(cur, cur.fetchone())

            cur.execute("""
                SELECT
                    COUNT(*) FILTER (
                        WHERE status = 'scheduled' AND due_at <= CURRENT_TIMESTAMP
                    ) AS due_reviews,
                    COUNT(*) FILTER (WHERE status = 'scheduled') AS scheduled_reviews,
                    (SELECT COUNT(*) FROM knowledge_learning_review_attempts
                     WHERE owner_id = %s
                       AND reviewed_at >= CURRENT_TIMESTAMP - (%s * interval '1 day')) AS review_attempts_period
                FROM knowledge_learning_reviews WHERE owner_id = %s
            """, (owner_id, days, owner_id))
            reviews = _row(cur, cur.fetchone())

            cur.execute("""
                SELECT status, COUNT(*) AS count
                FROM knowledge_learning_knowledge_candidates
                WHERE owner_id = %s GROUP BY status
            """, (owner_id,))
            candidate_counts = {row[0]: row[1] for row in cur.fetchall()}

            cur.execute("""
                SELECT assessment, COUNT(*) AS count
                FROM knowledge_learning_attempts
                WHERE owner_id = %s
                  AND created_at >= CURRENT_TIMESTAMP - (%s * interval '1 day')
                GROUP BY assessment ORDER BY assessment
            """, (owner_id, days))
            assessment_counts = {row[0]: row[1] for row in cur.fetchall()}

            cur.execute("""
                SELECT source_type, COUNT(*) AS count
                FROM knowledge_learning_sources
                WHERE owner_id = %s GROUP BY source_type ORDER BY source_type
            """, (owner_id,))
            source_counts = {row[0]: row[1] for row in cur.fetchall()}

            cur.execute("""
                SELECT s.topic,
                       COUNT(DISTINCT s.session_id) AS session_count,
                       COUNT(a.attempt_id) AS attempt_count,
                       COUNT(a.attempt_id) FILTER (WHERE a.assessment = 'misconception') AS misconception_labels,
                       MAX(s.updated_at) AS last_activity_at
                FROM knowledge_learning_sessions s
                LEFT JOIN knowledge_learning_attempts a
                  ON a.session_id = s.session_id AND a.owner_id = s.owner_id
                WHERE s.owner_id = %s
                GROUP BY s.topic ORDER BY MAX(s.updated_at) DESC LIMIT 20
            """, (owner_id,))
            topics = [_row(cur, value) for value in cur.fetchall()]

            cur.execute("""
                SELECT s.session_id, s.topic, s.effective_scope, s.status,
                       s.started_at, s.updated_at, s.completed_at,
                       COUNT(DISTINCT q.question_id) AS question_count,
                       COUNT(DISTINCT a.attempt_id) AS attempt_count
                FROM knowledge_learning_sessions s
                LEFT JOIN knowledge_learning_questions q
                  ON q.session_id = s.session_id AND q.owner_id = s.owner_id
                LEFT JOIN knowledge_learning_attempts a
                  ON a.session_id = s.session_id AND a.owner_id = s.owner_id
                WHERE s.owner_id = %s
                GROUP BY s.session_id
                ORDER BY s.updated_at DESC LIMIT 10
            """, (owner_id,))
            recent_sessions = [_row(cur, value) for value in cur.fetchall()]

        return {
            "period_days": days,
            "sessions": sessions,
            "reviews": reviews,
            "knowledge_candidates": {
                status: candidate_counts.get(status, 0)
                for status in ("pending", "approved", "rejected", "committing", "committed")
            },
            "client_llm_assessments": {
                status: assessment_counts.get(status, 0)
                for status in ("mastered", "partial", "misconception", "unknown", "unverifiable")
            },
            "source_counts": {"inbox": source_counts.get("inbox", 0), "knowledge": source_counts.get("knowledge", 0)},
            "topics": topics,
            "recent_sessions": recent_sessions,
        }
