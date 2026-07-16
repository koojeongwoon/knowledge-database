import json
from pathlib import Path
from typing import Any, Dict, List

from pydantic import BaseModel, Field

from src.core.config import current_user_config
from src.retrieval.evaluation import BlindQuery, blind_query_fingerprint, load_evaluation_cases


class GeneratedQuestion(BaseModel):
    case_id: str
    query: str
    query_type: str


class GeneratedQuestionBatch(BaseModel):
    questions: List[GeneratedQuestion]


def _sample_documents(db_manager, owner_id: str, excluded_paths: List[str], seed: str, limit: int):
    qa_limit = max(1, round(limit * 0.75))
    topic_limit = limit - qa_limit
    with db_manager.cursor() as cur:
        cur.execute(
            """
            WITH unique_docs AS (
                SELECT DISTINCT ON (file_path)
                    file_path, doc_type, title, description, parent_content, visibility
                FROM knowledge_documents
                WHERE owner_id = %s AND file_path <> ALL(%s)
                ORDER BY file_path, chunk_index
            ), sampled AS (
                (SELECT * FROM unique_docs WHERE file_path LIKE 'qa/%%'
                 ORDER BY md5(file_path || %s) LIMIT %s)
                UNION ALL
                (SELECT * FROM unique_docs WHERE file_path LIKE 'topics/%%'
                 ORDER BY md5(file_path || %s) LIMIT %s)
            )
            SELECT * FROM sampled ORDER BY md5(file_path || %s);
            """,
            (owner_id, excluded_paths, seed, qa_limit, seed, topic_limit, seed),
        )
        columns = [column[0] for column in cur.description]
        return [dict(zip(columns, row)) for row in cur.fetchall()]


def generate_blind_dataset(
    db_manager,
    development_cases_path: str,
    query_output: str,
    answer_output: str,
    seed: str = "blind-v1",
    answer_cases: int = 40,
    no_answer_cases: int = 10,
    exclude_answer_files: List[str] | None = None,
    query_types: List[str] | None = None,
) -> Dict[str, Any]:
    from openai import OpenAI

    config = current_user_config.get() or {}
    owner_id = config.get("user_id")
    api_key = config.get("openai_api_key")
    if not owner_id or not api_key:
        raise RuntimeError("Owner context with OpenAI API key is required.")

    development = load_evaluation_cases(development_cases_path)
    excluded = {path for case in development for path in case.expected_paths}
    for answer_file in exclude_answer_files or []:
        payload = json.loads(Path(answer_file).read_text(encoding="utf-8"))
        excluded.update(
            path for answer in payload.get("answers", [])
            for path in answer.get("expected_paths", [])
        )
    excluded = sorted(excluded)
    documents = _sample_documents(db_manager, owner_id, excluded, seed, answer_cases)
    if len(documents) < answer_cases:
        raise RuntimeError(f"Only {len(documents)} eligible blind documents were found.")

    client = OpenAI(api_key=api_key)
    query_types = query_types or ["exact", "semantic", "cross-language", "acronym", "mixed-language"]
    generated: Dict[str, GeneratedQuestion] = {}
    for start in range(0, len(documents), 5):
        batch = documents[start:start + 5]
        items = []
        for offset, doc in enumerate(batch):
            index = start + offset
            case_id = f"blind-answer-{index + 1:03d}"
            query_type = query_types[index % len(query_types)]
            content = (doc.get("parent_content") or "")[:6000]
            items.append(
                f"CASE_ID: {case_id}\nQUERY_TYPE: {query_type}\n"
                f"TITLE: {doc['title']}\nDESCRIPTION: {doc.get('description') or ''}\nCONTENT:\n{content}"
            )
        response = client.beta.chat.completions.parse(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": (
                    "You are an independent Korean search-quality evaluator. Create exactly one natural user query "
                    "per CASE_ID that is answerable only from its supplied document. Preserve CASE_ID and QUERY_TYPE. "
                    "For exact use distinctive terminology; semantic must paraphrase without copying the title; "
                    "cross-language must be English; acronym should use a meaningful acronym; mixed-language should mix Korean and English. "
                    "Do not mention file paths and do not reveal the answer."
                )},
                {"role": "user", "content": "\n\n---\n\n".join(items)},
            ],
            response_format=GeneratedQuestionBatch,
            temperature=0.7,
        )
        parsed = response.choices[0].message.parsed
        if not parsed:
            raise RuntimeError("Question generator returned no parsed output.")
        for question in parsed.questions:
            generated[question.case_id] = question

    no_answer_ids = [f"blind-no-answer-{index + 1:03d}" for index in range(no_answer_cases)]
    response = client.beta.chat.completions.parse(
        model="gpt-4o-mini",
        messages=[
            {"role": "system", "content": (
                "You are an independent search evaluator. For every supplied CASE_ID create one specific natural Korean question "
                "about domains unlikely to exist in a software engineering personal wiki: zoological population measurements, "
                "high-temperature material constants, obscure ancient manuscripts, regional agricultural statistics, or astronomical observations. "
                "Preserve CASE_ID and set query_type to no-answer. Avoid generic words that overlap software topics."
            )},
            {"role": "user", "content": "CASE_ID values: " + ", ".join(no_answer_ids)},
        ],
        response_format=GeneratedQuestionBatch,
        temperature=0.8,
    )
    parsed = response.choices[0].message.parsed
    if not parsed:
        raise RuntimeError("No-answer generator returned no parsed output.")
    for question in parsed.questions:
        generated[question.case_id] = question

    expected_ids = {f"blind-answer-{i + 1:03d}" for i in range(answer_cases)} | set(no_answer_ids)
    if set(generated) != expected_ids:
        raise RuntimeError("Generator returned missing, duplicate, or unexpected case IDs.")

    ordered_questions = [generated[case_id] for case_id in sorted(expected_ids)]
    blind_queries = [BlindQuery(q.case_id, q.query, q.query_type) for q in ordered_questions]
    fingerprint = blind_query_fingerprint(blind_queries)
    query_payload = {
        "version": 1, "split": "blind", "seed": seed,
        "cases": [{"id": q.case_id, "query": q.query, "query_type": q.query_type} for q in ordered_questions],
    }
    answers = []
    for index, doc in enumerate(documents):
        answers.append({"id": f"blind-answer-{index + 1:03d}", "expected_paths": [doc["file_path"]]})
    answers.extend({"id": case_id, "expected_paths": [], "expected_no_answer": True} for case_id in no_answer_ids)
    answer_payload = {"version": 1, "query_fingerprint": fingerprint, "answers": answers}
    Path(query_output).write_text(json.dumps(query_payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    Path(answer_output).write_text(json.dumps(answer_payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return {"cases": len(ordered_questions), "answer_cases": answer_cases, "no_answer_cases": no_answer_cases, "query_fingerprint": fingerprint}
