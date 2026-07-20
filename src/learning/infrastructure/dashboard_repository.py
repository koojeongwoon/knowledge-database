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
                SELECT
                    CASE
                        WHEN learning_dimension = 'transfer' THEN transfer_level || '_transfer'
                        ELSE learning_dimension
                    END AS evidence_key,
                    COUNT(*) AS attempt_count,
                    COUNT(*) FILTER (
                        WHERE assessment = 'mastered' AND independent_success = TRUE
                    ) AS independent_mastery_count
                FROM knowledge_learning_attempts
                WHERE owner_id = %s
                  AND created_at >= CURRENT_TIMESTAMP - (%s * interval '1 day')
                GROUP BY evidence_key ORDER BY evidence_key
            """, (owner_id, days))
            learning_evidence = {
                row[0]: {"attempt_count": row[1], "independent_mastery_count": row[2]}
                for row in cur.fetchall()
            }

            cur.execute("""
                SELECT transfer_level,
                       COUNT(*) FILTER (WHERE status = 'scheduled') AS scheduled_count,
                       COUNT(*) FILTER (
                           WHERE status = 'scheduled' AND due_at <= CURRENT_TIMESTAMP
                       ) AS due_count
                FROM knowledge_learning_reviews
                WHERE owner_id = %s
                GROUP BY transfer_level ORDER BY transfer_level
            """, (owner_id,))
            delayed_transfer_reviews = {
                row[0]: {"scheduled_count": row[1], "due_count": row[2],
                         "attempt_count": 0, "independent_mastery_count": 0}
                for row in cur.fetchall()
            }
            cur.execute("""
                SELECT transfer_level, COUNT(*) AS attempt_count,
                       COUNT(*) FILTER (
                           WHERE assessment = 'mastered' AND independent_success = TRUE
                       ) AS independent_mastery_count
                FROM knowledge_learning_review_attempts
                WHERE owner_id = %s
                  AND reviewed_at >= CURRENT_TIMESTAMP - (%s * interval '1 day')
                GROUP BY transfer_level ORDER BY transfer_level
            """, (owner_id, days))
            for level, attempt_count, independent_count in cur.fetchall():
                metrics = delayed_transfer_reviews.setdefault(
                    level, {"scheduled_count": 0, "due_count": 0,
                            "attempt_count": 0, "independent_mastery_count": 0},
                )
                metrics["attempt_count"] = attempt_count
                metrics["independent_mastery_count"] = independent_count

            cur.execute("""
                SELECT calibration_signal, COUNT(*) AS count
                FROM (
                    SELECT calibration_signal, created_at AS occurred_at
                    FROM knowledge_learning_attempts WHERE owner_id = %s
                    UNION ALL
                    SELECT calibration_signal, reviewed_at AS occurred_at
                    FROM knowledge_learning_review_attempts WHERE owner_id = %s
                ) calibration_events
                WHERE occurred_at >= CURRENT_TIMESTAMP - (%s * interval '1 day')
                GROUP BY calibration_signal ORDER BY calibration_signal
            """, (owner_id, owner_id, days))
            calibration_counts = {row[0]: row[1] for row in cur.fetchall()}

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
                SELECT s.topic,
                       COUNT(*) FILTER (WHERE a.learning_dimension = 'retrieval'
                           AND a.assessment = 'mastered' AND a.independent_success = TRUE) AS retrieval,
                       COUNT(*) FILTER (WHERE a.learning_dimension = 'comprehension'
                           AND a.assessment = 'mastered' AND a.independent_success = TRUE) AS comprehension,
                       COUNT(*) FILTER (WHERE a.learning_dimension = 'transfer' AND a.transfer_level = 'near'
                           AND a.assessment = 'mastered' AND a.independent_success = TRUE) AS near_transfer,
                       COUNT(*) FILTER (WHERE a.learning_dimension = 'transfer' AND a.transfer_level = 'far'
                           AND a.assessment = 'mastered' AND a.independent_success = TRUE) AS far_transfer
                FROM knowledge_learning_sessions s
                LEFT JOIN knowledge_learning_attempts a
                  ON a.session_id = s.session_id AND a.owner_id = s.owner_id
                WHERE s.owner_id = %s
                GROUP BY s.topic
            """, (owner_id,))
            topic_evidence = {
                row[0]: {"retrieval": row[1], "comprehension": row[2],
                         "near_transfer": row[3], "far_transfer": row[4], "far_review": 0}
                for row in cur.fetchall()
            }
            cur.execute("""
                SELECT r.topic,
                       COUNT(*) FILTER (WHERE ra.transfer_level = 'far'
                           AND ra.assessment = 'mastered' AND ra.independent_success = TRUE) AS far_review
                FROM knowledge_learning_reviews r
                LEFT JOIN knowledge_learning_review_attempts ra
                  ON ra.review_id = r.review_id AND ra.owner_id = r.owner_id
                WHERE r.owner_id = %s
                GROUP BY r.topic
            """, (owner_id,))
            for topic, far_review in cur.fetchall():
                topic_evidence.setdefault(
                    topic, {"retrieval": 0, "comprehension": 0,
                            "near_transfer": 0, "far_transfer": 0, "far_review": 0},
                )["far_review"] = far_review

            cur.execute("""
                SELECT s.topic, misconception, COUNT(*) AS occurrence_count,
                       MAX(a.created_at) AS last_seen_at
                FROM knowledge_learning_attempts a
                JOIN knowledge_learning_sessions s
                  ON s.session_id = a.session_id AND s.owner_id = a.owner_id
                CROSS JOIN LATERAL unnest(a.misconceptions) AS misconception
                WHERE a.owner_id = %s AND btrim(misconception) <> ''
                GROUP BY s.topic, misconception
                HAVING COUNT(*) >= 2
                ORDER BY COUNT(*) DESC, MAX(a.created_at) DESC
                LIMIT 50
            """, (owner_id,))
            recurring_misconceptions: Dict[str, List[Dict[str, Any]]] = {}
            for topic, misconception, count, last_seen_at in cur.fetchall():
                recurring_misconceptions.setdefault(topic, []).append({
                    "misconception": misconception, "occurrence_count": count,
                    "last_seen_at": last_seen_at.isoformat() if hasattr(last_seen_at, "isoformat") else last_seen_at,
                })

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
            "learning_evidence": {
                key: learning_evidence.get(key, {"attempt_count": 0, "independent_mastery_count": 0})
                for key in ("retrieval", "comprehension", "near_transfer", "far_transfer")
            },
            "delayed_transfer_reviews": {
                level: delayed_transfer_reviews.get(
                    level, {"scheduled_count": 0, "due_count": 0,
                            "attempt_count": 0, "independent_mastery_count": 0},
                )
                for level in ("near", "far")
            },
            "metacognitive_calibration": {
                signal: calibration_counts.get(signal, 0)
                for signal in ("aligned", "overconfident", "underconfident", "insufficient_evidence")
            },
            "source_counts": {"inbox": source_counts.get("inbox", 0), "knowledge": source_counts.get("knowledge", 0)},
            "topics": topics,
            "topic_mastery_inputs": topic_evidence,
            "recurring_misconceptions": recurring_misconceptions,
            "recent_sessions": recent_sessions,
        }
