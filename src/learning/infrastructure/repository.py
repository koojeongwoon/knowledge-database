import json
import uuid
from typing import Any, Dict, List, Optional

from src.learning.domain.review import next_review_interval


def _row(cur, value) -> Optional[Dict[str, Any]]:
    if not value:
        return None
    columns = [column[0] for column in cur.description]
    result = dict(zip(columns, value))
    for key, item in list(result.items()):
        if isinstance(item, uuid.UUID):
            result[key] = str(item)
        elif hasattr(item, "isoformat"):
            result[key] = item.isoformat()
        elif isinstance(item, str) and key in {"plan_snapshot", "feedback_plan", "metadata"}:
            try:
                result[key] = json.loads(item)
            except json.JSONDecodeError:
                pass
    return result


class LearningSessionRepository:
    def __init__(self, db_manager):
        self.db_manager = db_manager

    def start(self, owner_id: str, session: Dict[str, Any], question: Dict[str, Any], sources: List[Dict[str, Any]]) -> Dict[str, Any]:
        with self.db_manager.transaction() as cur:
            request_id = session.get("client_request_id")
            if request_id:
                cur.execute(
                    "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0));",
                    (f"learning-session:{owner_id}:{request_id}",),
                )
                cur.execute("""
                    SELECT session_id, status FROM knowledge_learning_sessions
                    WHERE owner_id = %s AND client_request_id = %s
                """, (owner_id, request_id))
                existing = _row(cur, cur.fetchone())
                if existing:
                    return {"session_id": existing["session_id"], "status": existing["status"], "idempotent_replay": True}

            cur.execute("""
                INSERT INTO knowledge_learning_sessions (
                    session_id, owner_id, client_request_id, topic, requested_scope,
                    effective_scope, goal, level, duration_minutes, plan_snapshot
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb)
                RETURNING session_id, status, started_at
            """, (
                session["session_id"], owner_id, request_id, session["topic"],
                session["requested_scope"], session["effective_scope"], session["goal"],
                session["level"], session["duration_minutes"], json.dumps(session["plan_snapshot"]),
            ))
            created = _row(cur, cur.fetchone())
            cur.execute("""
                INSERT INTO knowledge_learning_questions (
                    question_id, session_id, owner_id, sequence_no, question_type, prompt, evidence_refs
                ) VALUES (%s, %s, %s, 1, %s, %s, %s)
            """, (
                question["question_id"], session["session_id"], owner_id,
                question["question_type"], question["prompt"], question["evidence_refs"],
            ))
            for source in sources:
                cur.execute("""
                    INSERT INTO knowledge_learning_sources (
                        session_id, owner_id, source_type, source_ref, relationship, snapshot_hash, metadata
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s::jsonb)
                """, (
                    session["session_id"], owner_id, source["source_type"], source["source_ref"],
                    source.get("relationship"), source["snapshot_hash"], json.dumps(source.get("metadata") or {}),
                ))
            return {**created, "question_id": question["question_id"], "idempotent_replay": False}

    def record_attempt(
        self, owner_id: str, attempt: Dict[str, Any], next_question: Optional[Dict[str, Any]],
    ) -> Dict[str, Any]:
        with self.db_manager.transaction() as cur:
            request_id = attempt.get("client_request_id")
            if request_id:
                cur.execute(
                    "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0));",
                    (f"learning-attempt:{owner_id}:{request_id}",),
                )
                cur.execute("""
                    SELECT attempt_id, session_id, question_id, created_at
                    FROM knowledge_learning_attempts
                    WHERE owner_id = %s AND client_request_id = %s
                """, (owner_id, request_id))
                existing = _row(cur, cur.fetchone())
                if existing:
                    cur.execute("""
                        SELECT review_id, review_priority, interval_days, due_at
                        FROM knowledge_learning_reviews
                        WHERE source_attempt_id = %s AND owner_id = %s
                    """, (existing["attempt_id"], owner_id))
                    scheduled_review = _row(cur, cur.fetchone())
                    return {
                        **existing, "idempotent_replay": True,
                        "scheduled_review": scheduled_review, "next_question": None,
                    }

            cur.execute("""
                SELECT status, topic FROM knowledge_learning_sessions
                WHERE session_id = %s AND owner_id = %s FOR UPDATE
            """, (attempt["session_id"], owner_id))
            session = cur.fetchone()
            if not session:
                raise KeyError("학습 세션을 찾을 수 없습니다.")
            if session[0] != "active":
                raise ValueError("완료된 학습 세션에는 답변을 추가할 수 없습니다.")
            cur.execute("""
                SELECT sequence_no, prompt, evidence_refs FROM knowledge_learning_questions
                WHERE question_id = %s AND session_id = %s AND owner_id = %s
            """, (attempt["question_id"], attempt["session_id"], owner_id))
            question = cur.fetchone()
            if not question:
                raise KeyError("학습 질문을 찾을 수 없습니다.")

            cur.execute("""
                INSERT INTO knowledge_learning_attempts (
                    attempt_id, session_id, question_id, owner_id, client_request_id,
                    answer, assessment, confidence, missing_concepts, misconceptions,
                    evidence_refs, feedback_plan
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb)
                RETURNING attempt_id, session_id, question_id, created_at
            """, (
                attempt["attempt_id"], attempt["session_id"], attempt["question_id"], owner_id,
                request_id, attempt["answer"], attempt["assessment"], attempt["confidence"],
                attempt["missing_concepts"], attempt["misconceptions"], attempt["evidence_refs"],
                json.dumps(attempt["feedback_plan"]),
            ))
            created = _row(cur, cur.fetchone())
            review_schedule = attempt.get("review_schedule")
            scheduled_review = None
            if review_schedule:
                cur.execute("""
                    INSERT INTO knowledge_learning_reviews (
                        review_id, owner_id, session_id, question_id, source_attempt_id,
                        topic, prompt, evidence_refs, review_priority, interval_days, due_at
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                              CURRENT_TIMESTAMP + (%s * interval '1 day'))
                    ON CONFLICT (owner_id, question_id) DO UPDATE SET
                        source_attempt_id = EXCLUDED.source_attempt_id,
                        topic = EXCLUDED.topic,
                        prompt = EXCLUDED.prompt,
                        evidence_refs = EXCLUDED.evidence_refs,
                        review_priority = EXCLUDED.review_priority,
                        interval_days = EXCLUDED.interval_days,
                        due_at = EXCLUDED.due_at,
                        status = 'scheduled',
                        updated_at = CURRENT_TIMESTAMP
                    RETURNING review_id, review_priority, interval_days, due_at
                """, (
                    str(uuid.uuid4()), owner_id, attempt["session_id"], attempt["question_id"],
                    attempt["attempt_id"], session[1], question[1], question[2],
                    review_schedule["review_priority"], review_schedule["interval_days"],
                    review_schedule["interval_days"],
                ))
                scheduled_review = _row(cur, cur.fetchone())
            created_question = None
            if next_question:
                cur.execute("""
                    SELECT COALESCE(MAX(sequence_no), 0) + 1
                    FROM knowledge_learning_questions
                    WHERE session_id = %s AND owner_id = %s
                """, (attempt["session_id"], owner_id))
                next_sequence = cur.fetchone()[0]
                cur.execute("""
                    INSERT INTO knowledge_learning_questions (
                        question_id, session_id, owner_id, sequence_no, question_type, prompt, evidence_refs
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s)
                    RETURNING question_id, sequence_no, question_type, prompt, evidence_refs, created_at
                """, (
                    next_question["question_id"], attempt["session_id"], owner_id, next_sequence,
                    next_question["question_type"], next_question["prompt"], next_question["evidence_refs"],
                ))
                created_question = _row(cur, cur.fetchone())
            cur.execute("""
                UPDATE knowledge_learning_sessions SET updated_at = CURRENT_TIMESTAMP
                WHERE session_id = %s AND owner_id = %s
            """, (attempt["session_id"], owner_id))
            return {
                **created, "idempotent_replay": False,
                "scheduled_review": scheduled_review, "next_question": created_question,
            }

    def resume(self, owner_id: str, session_id: Optional[str] = None) -> Dict[str, Any]:
        with self.db_manager.cursor() as cur:
            if session_id:
                cur.execute("""
                    SELECT * FROM knowledge_learning_sessions
                    WHERE session_id = %s AND owner_id = %s
                """, (session_id, owner_id))
            else:
                cur.execute("""
                    SELECT * FROM knowledge_learning_sessions
                    WHERE owner_id = %s AND status = 'active'
                    ORDER BY updated_at DESC LIMIT 1
                """, (owner_id,))
            session = _row(cur, cur.fetchone())
            if not session:
                raise KeyError("이어갈 학습 세션을 찾을 수 없습니다.")
            cur.execute("""
                SELECT source_type, source_ref, relationship, snapshot_hash, metadata
                FROM knowledge_learning_sources
                WHERE session_id = %s AND owner_id = %s ORDER BY source_type, source_ref
            """, (session["session_id"], owner_id))
            sources = [_row(cur, value) for value in cur.fetchall()]
            cur.execute("""
                SELECT q.question_id, q.sequence_no, q.question_type, q.prompt, q.evidence_refs,
                       a.attempt_id, a.answer, a.assessment, a.confidence, a.missing_concepts,
                       a.misconceptions, a.evidence_refs AS attempt_evidence_refs,
                       a.feedback_plan, a.created_at AS attempted_at
                FROM knowledge_learning_questions q
                LEFT JOIN LATERAL (
                    SELECT * FROM knowledge_learning_attempts a
                    WHERE a.question_id = q.question_id AND a.owner_id = %s
                    ORDER BY a.created_at DESC LIMIT 1
                ) a ON TRUE
                WHERE q.session_id = %s AND q.owner_id = %s
                ORDER BY q.sequence_no
            """, (owner_id, session["session_id"], owner_id))
            questions = [_row(cur, value) for value in cur.fetchall()]
            return {"session": session, "sources": sources, "questions": questions}

    def complete(self, owner_id: str, session_id: str, summary: Optional[str]) -> Dict[str, Any]:
        with self.db_manager.transaction() as cur:
            cur.execute("""
                UPDATE knowledge_learning_sessions
                SET status = 'completed', completion_summary = %s,
                    completed_at = CURRENT_TIMESTAMP, updated_at = CURRENT_TIMESTAMP
                WHERE session_id = %s AND owner_id = %s AND status = 'active'
                RETURNING session_id, status, completed_at
            """, (summary, session_id, owner_id))
            session = _row(cur, cur.fetchone())
            if not session:
                cur.execute("""
                    SELECT status FROM knowledge_learning_sessions
                    WHERE session_id = %s AND owner_id = %s
                """, (session_id, owner_id))
                existing = cur.fetchone()
                if not existing:
                    raise KeyError("학습 세션을 찾을 수 없습니다.")
                return {"session_id": session_id, "status": existing[0], "idempotent_replay": True}
            cur.execute("""
                SELECT
                    (SELECT COUNT(*) FROM knowledge_learning_questions
                     WHERE session_id = %s AND owner_id = %s),
                    (SELECT COUNT(*) FROM knowledge_learning_attempts
                     WHERE session_id = %s AND owner_id = %s)
            """, (session_id, owner_id, session_id, owner_id))
            question_count, attempt_count = cur.fetchone()
            return {**session, "question_count": question_count, "attempt_count": attempt_count, "idempotent_replay": False}

    def list_due_reviews(self, owner_id: str, limit: int) -> List[Dict[str, Any]]:
        with self.db_manager.cursor() as cur:
            cur.execute("""
                SELECT review_id, session_id, question_id, topic, prompt, evidence_refs,
                       review_priority, interval_days, due_at, review_count, last_reviewed_at
                FROM knowledge_learning_reviews
                WHERE owner_id = %s AND status = 'scheduled' AND due_at <= CURRENT_TIMESTAMP
                ORDER BY
                    CASE review_priority
                        WHEN 'critical' THEN 1 WHEN 'high' THEN 2 WHEN 'medium' THEN 3
                        WHEN 'low' THEN 4 ELSE 5
                    END,
                    due_at
                LIMIT %s
            """, (owner_id, limit))
            return [_row(cur, value) for value in cur.fetchall()]

    def record_review(self, owner_id: str, review: Dict[str, Any]) -> Dict[str, Any]:
        with self.db_manager.transaction() as cur:
            request_id = review.get("client_request_id")
            if request_id:
                cur.execute(
                    "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0));",
                    (f"learning-review:{owner_id}:{request_id}",),
                )
                cur.execute("""
                    SELECT review_attempt_id, review_id, next_interval_days, reviewed_at
                    FROM knowledge_learning_review_attempts
                    WHERE owner_id = %s AND client_request_id = %s
                """, (owner_id, request_id))
                existing = _row(cur, cur.fetchone())
                if existing:
                    return {**existing, "idempotent_replay": True}

            cur.execute("""
                SELECT interval_days, status FROM knowledge_learning_reviews
                WHERE review_id = %s AND owner_id = %s FOR UPDATE
            """, (review["review_id"], owner_id))
            current = cur.fetchone()
            if not current:
                raise KeyError("복습 항목을 찾을 수 없습니다.")
            if current[1] != "scheduled":
                raise ValueError("중지된 복습 항목은 기록할 수 없습니다.")
            previous_days = current[0]
            next_days = next_review_interval(previous_days, review["assessment"], review["confidence"])
            review_attempt_id = str(uuid.uuid4())
            cur.execute("""
                INSERT INTO knowledge_learning_review_attempts (
                    review_attempt_id, review_id, owner_id, client_request_id, answer,
                    assessment, confidence, feedback_plan, previous_interval_days, next_interval_days
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s, %s)
                RETURNING review_attempt_id, review_id, reviewed_at
            """, (
                review_attempt_id, review["review_id"], owner_id, request_id, review["answer"],
                review["assessment"], review["confidence"], json.dumps(review["feedback_plan"]),
                previous_days, next_days,
            ))
            created = _row(cur, cur.fetchone())
            cur.execute("""
                UPDATE knowledge_learning_reviews
                SET interval_days = %s,
                    due_at = CURRENT_TIMESTAMP + (%s * interval '1 day'),
                    review_count = review_count + 1,
                    last_reviewed_at = CURRENT_TIMESTAMP,
                    review_priority = %s,
                    updated_at = CURRENT_TIMESTAMP
                WHERE review_id = %s AND owner_id = %s
            """, (
                next_days, next_days, review["review_priority"], review["review_id"], owner_id,
            ))
            return {
                **created, "previous_interval_days": previous_days,
                "next_interval_days": next_days, "idempotent_replay": False,
            }

    def stage_knowledge_candidates(
        self, owner_id: str, session_id: str, candidates: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        with self.db_manager.transaction() as cur:
            cur.execute("""
                SELECT status FROM knowledge_learning_sessions
                WHERE session_id = %s AND owner_id = %s
            """, (session_id, owner_id))
            if not cur.fetchone():
                raise KeyError("학습 세션을 찾을 수 없습니다.")
            results = []
            for candidate in candidates:
                request_id = candidate.get("client_request_id")
                if request_id:
                    cur.execute(
                        "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0));",
                        (f"learning-candidate:{owner_id}:{request_id}",),
                    )
                cur.execute("""
                    INSERT INTO knowledge_learning_knowledge_candidates (
                        candidate_id, owner_id, session_id, client_request_id, candidate_type,
                        title, description, tags, content, topic_name, topic_update_text,
                        evidence_refs, content_hash
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (owner_id, client_request_id) DO UPDATE SET
                        updated_at = knowledge_learning_knowledge_candidates.updated_at
                    RETURNING candidate_id, candidate_type, title, status, created_at
                """, (
                    candidate["candidate_id"], owner_id, session_id, request_id,
                    candidate["candidate_type"], candidate["title"], candidate["description"],
                    candidate["tags"], candidate["content"], candidate.get("topic_name"),
                    candidate.get("topic_update_text"), candidate["evidence_refs"], candidate["content_hash"],
                ))
                results.append(_row(cur, cur.fetchone()))
            return results

    def list_knowledge_candidates(self, owner_id: str, session_id: str) -> List[Dict[str, Any]]:
        with self.db_manager.cursor() as cur:
            cur.execute("""
                SELECT candidate_id, candidate_type, title, description, tags, content,
                       topic_name, topic_update_text, evidence_refs, content_hash, status,
                       approval_note, approved_at, rejected_at, committed_at,
                       qa_file_path, topic_file_path, created_at, updated_at
                FROM knowledge_learning_knowledge_candidates
                WHERE session_id = %s AND owner_id = %s
                ORDER BY created_at, candidate_id
            """, (session_id, owner_id))
            return [_row(cur, value) for value in cur.fetchall()]

    def review_knowledge_candidate(
        self, owner_id: str, candidate_id: str, approved: bool, note: Optional[str],
    ) -> Dict[str, Any]:
        target = "approved" if approved else "rejected"
        timestamp_column = "approved_at" if approved else "rejected_at"
        with self.db_manager.transaction() as cur:
            cur.execute(f"""
                UPDATE knowledge_learning_knowledge_candidates
                SET status = %s, approval_note = %s, {timestamp_column} = CURRENT_TIMESTAMP,
                    updated_at = CURRENT_TIMESTAMP
                WHERE candidate_id = %s AND owner_id = %s AND status = 'pending'
                RETURNING candidate_id, candidate_type, title, status, approval_note, {timestamp_column}
            """, (target, note, candidate_id, owner_id))
            result = _row(cur, cur.fetchone())
            if result:
                return {**result, "idempotent_replay": False}
            cur.execute("""
                SELECT candidate_id, candidate_type, title, status, approval_note
                FROM knowledge_learning_knowledge_candidates
                WHERE candidate_id = %s AND owner_id = %s
            """, (candidate_id, owner_id))
            existing = _row(cur, cur.fetchone())
            if not existing:
                raise KeyError("재지식화 후보를 찾을 수 없습니다.")
            if existing["status"] == target:
                return {**existing, "idempotent_replay": True}
            raise ValueError(f"{existing['status']} 상태의 후보는 {target} 상태로 변경할 수 없습니다.")

    def claim_approved_knowledge_candidate(self, owner_id: str, candidate_id: str) -> Dict[str, Any]:
        with self.db_manager.transaction() as cur:
            cur.execute("""
                UPDATE knowledge_learning_knowledge_candidates
                SET status = 'committing', updated_at = CURRENT_TIMESTAMP
                WHERE candidate_id = %s AND owner_id = %s AND status = 'approved'
                RETURNING candidate_id, session_id, candidate_type, title, description, tags,
                          content, topic_name, topic_update_text, evidence_refs, content_hash, status,
                          qa_file_path, topic_file_path
            """, (candidate_id, owner_id))
            candidate = _row(cur, cur.fetchone())
            if candidate:
                return candidate
            cur.execute("""
                SELECT candidate_id, session_id, candidate_type, title, description, tags,
                       content, topic_name, topic_update_text, evidence_refs, content_hash, status,
                       qa_file_path, topic_file_path
                FROM knowledge_learning_knowledge_candidates
                WHERE candidate_id = %s AND owner_id = %s
            """, (candidate_id, owner_id))
            candidate = _row(cur, cur.fetchone())
            if not candidate:
                raise KeyError("재지식화 후보를 찾을 수 없습니다.")
        if candidate["status"] == "committed":
            return candidate
        if candidate["status"] == "committing":
            raise ValueError("해당 후보의 Knowledge 저장이 이미 진행 중입니다.")
        raise ValueError("사용자가 승인한 후보만 Knowledge로 저장할 수 있습니다.")

    def release_knowledge_candidate_claim(self, owner_id: str, candidate_id: str) -> None:
        with self.db_manager.transaction() as cur:
            cur.execute("""
                UPDATE knowledge_learning_knowledge_candidates
                SET status = 'approved', updated_at = CURRENT_TIMESTAMP
                WHERE candidate_id = %s AND owner_id = %s AND status = 'committing'
            """, (candidate_id, owner_id))

    def mark_knowledge_candidate_committed(
        self, owner_id: str, candidate_id: str, qa_file_path: str, topic_file_path: Optional[str],
    ) -> Dict[str, Any]:
        with self.db_manager.transaction() as cur:
            cur.execute("""
                UPDATE knowledge_learning_knowledge_candidates
                SET status = 'committed', qa_file_path = %s, topic_file_path = %s,
                    committed_at = CURRENT_TIMESTAMP, updated_at = CURRENT_TIMESTAMP
                WHERE candidate_id = %s AND owner_id = %s AND status = 'committing'
                RETURNING candidate_id, status, qa_file_path, topic_file_path, committed_at
            """, (qa_file_path, topic_file_path, candidate_id, owner_id))
            result = _row(cur, cur.fetchone())
            if result:
                return {**result, "idempotent_replay": False}
            cur.execute("""
                SELECT candidate_id, status, qa_file_path, topic_file_path, committed_at
                FROM knowledge_learning_knowledge_candidates
                WHERE candidate_id = %s AND owner_id = %s
            """, (candidate_id, owner_id))
            existing = _row(cur, cur.fetchone())
            if existing and existing["status"] == "committed":
                return {**existing, "idempotent_replay": True}
            raise ValueError("저장 진행 상태의 후보만 커밋 완료 처리할 수 있습니다.")
