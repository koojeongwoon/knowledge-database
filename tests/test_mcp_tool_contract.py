import ast
from pathlib import Path


EXPECTED_TOOLS = {
    "search_wiki_knowledge": ["query", "limit"],
    "prepare_knowledge_baseline": ["name", "version", "purpose", "source_paths", "base_release_id"],
    "confirm_knowledge_baseline": ["draft_id"],
    "search_knowledge_baseline": ["query", "release_id", "limit"],
    "create_inbox_markdown": ["title", "content", "source_kind", "original_filename", "original_url", "media_type", "extraction_complete", "warnings", "note"],
    "list_inbox_items": ["limit"], "read_inbox_item": ["item_id"],
    "prepare_learning_session": ["topic", "scope", "goal", "level", "duration_minutes", "inbox_item_ids", "knowledge_limit"],
    "plan_learning_feedback": ["assessment", "confidence", "missing_concepts", "misconceptions", "evidence_refs", "hint", "next_question", "learning_dimension", "transfer_level", "support_level"],
    "start_learning_session": ["topic", "requested_scope", "effective_scope", "goal", "level", "duration_minutes", "first_question", "sources", "client_request_id"],
    "record_learning_attempt": ["session_id", "question_id", "answer", "assessment", "confidence", "feedback_plan", "missing_concepts", "misconceptions", "evidence_refs", "next_question", "next_question_type", "next_evidence_refs", "client_request_id", "next_transfer_level"],
    "resume_learning_session": ["session_id"],
    "prepare_learning_completion": ["session_id"],
    "complete_learning_session": ["session_id", "summary"],
    "list_due_learning_reviews": ["limit"],
    "record_learning_review": ["review_id", "answer", "assessment", "confidence", "feedback_plan", "client_request_id"],
    "prepare_learning_knowledge_candidates": ["session_id"],
    "stage_learning_knowledge_candidates": ["session_id", "candidates"],
    "review_learning_knowledge_candidate": ["candidate_id", "approved", "note"],
    "commit_learning_knowledge_candidate": ["candidate_id"],
    "submit_search_feedback": ["search_id", "relevant_paths", "irrelevant_paths", "expected_no_answer", "missing_answer_path", "notes", "partially_relevant_paths", "satisfaction", "failure_reasons", "result_feedback", "expected_relations", "expected_graph_paths", "forbidden_paths", "expected_rule_types", "ontology_notes"],
    "commit_new_knowledge": ["title", "description", "tags", "content", "topic_name", "topic_update_text", "visibility"],
    "run_database_indexing": ["file_paths"],
}


def test_public_mcp_tool_names_and_input_fields_are_stable() -> None:
    tree = ast.parse(Path("src/api/mcp_server.py").read_text(encoding="utf-8"))
    actual = {}
    for node in tree.body:
        if not isinstance(node, ast.FunctionDef):
            continue
        for decorator in node.decorator_list:
            if not (isinstance(decorator, ast.Call) and isinstance(decorator.func, ast.Attribute)
                    and decorator.func.attr == "tool"):
                continue
            name = next(
                keyword.value.value for keyword in decorator.keywords
                if keyword.arg == "name" and isinstance(keyword.value, ast.Constant)
            )
            actual[name] = [argument.arg for argument in node.args.args]
    assert actual == EXPECTED_TOOLS
