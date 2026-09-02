import json
import sys
from pathlib import Path
from src.core.database.factory import DatabaseManager
from src.indexing.composition import create_wiki_indexer
from src.retrieval.composition import create_wiki_searcher
from src.core.config import EMBEDDING_DIM, EMBEDDING_PROVIDER
from src.cli.context import activate_owner_context, deactivate_owner_context


def get_embedding_service():
    """설정된 EMBEDDING_PROVIDER에 따라 알맞은 임베딩 서비스 구현체를 리턴합니다."""
    if EMBEDDING_PROVIDER == "openai":
        from src.indexing.domain.embedding import OpenAIEmbeddingService
        return OpenAIEmbeddingService(dimension=EMBEDDING_DIM)
    elif EMBEDDING_PROVIDER == "bge-m3":
        from src.indexing.domain.embedding import BGEM3EmbeddingService
        return BGEM3EmbeddingService()
    else:
        from src.indexing.domain.embedding import FakeEmbeddingService
        return FakeEmbeddingService(dimension=EMBEDDING_DIM)


def execute_index(owner_id: str) -> None:
    owner_token = activate_owner_context(owner_id)
    db_manager = DatabaseManager()
    try:
        embedding_service = get_embedding_service()
        indexer = create_wiki_indexer(db_manager, embedding_service)
        stats = indexer.run_indexing()
        print("\n=== Indexing Summary ===")
        print(f"  Created: {stats['created']}")
        print(f"  Updated: {stats['updated']}")
        print(f"  Deleted: {stats['deleted']}")
        print(f"  Skipped: {stats['skipped']}")
        print("=========================")
    except Exception as e:
        print(f"Error during indexing: {e}", file=sys.stderr)
        print("\nTIP: PostgreSQL이 실행 중인지, .env 파일의 DB 연결 정보가 올바른지 확인해주세요.", file=sys.stderr)
        sys.exit(1)
    finally:
        db_manager.close()
        deactivate_owner_context(owner_token)


def execute_search(query: str, owner_id: str, limit: int = 5) -> None:
    owner_token = activate_owner_context(owner_id)
    db_manager = DatabaseManager()
    try:
        embedding_service = get_embedding_service()
        searcher = create_wiki_searcher(db_manager, embedding_service)
        results = searcher.search(query, limit=limit)
        if not results:
            print("No matching documents found.")
            return
            
        print(f"\nFound {len(results)} matching document(s) for query: '{query}' (Provider: {EMBEDDING_PROVIDER})\n")
        for i, doc in enumerate(results, 1):
            print(f"{i}. [{doc['title']}] ({doc['file_path']})")
            print(f"   Similarity Score: {doc['similarity']:.4f} | Type: {doc['doc_type']}")
            if doc.get('description'):
                print(f"   Description: {doc['description']}")
            if doc.get('tags'):
                print(f"   Tags: {', '.join(doc['tags'])}")
            
            print("   " + "-" * 40)
            content_lines = doc['content'].strip().split('\n')
            preview = '\n'.join([f"   {line}" for line in content_lines[:3]])
            print(preview)
            if len(content_lines) > 3:
                print("   ...")
            print("   " + "-" * 40 + "\n")
    except Exception as e:
        print(f"Error during search: {e}", file=sys.stderr)
        print("\nTIP: PostgreSQL이 실행 중인지, .env 파일의 DB 연결 정보가 올바른지 확인해주세요.", file=sys.stderr)
        sys.exit(1)
    finally:
        db_manager.close()
        deactivate_owner_context(owner_token)


def execute_retry_indexing(limit: int = 100, force: bool = False) -> None:
    from src.api.agent_tool import retry_wiki_indexing

    response = json.loads(retry_wiki_indexing(limit=limit, force=force))
    if not response.get("success"):
        print(response.get("message", "Failed to retry indexing"), file=sys.stderr)
        sys.exit(1)

    data = response.get("data") or {}
    print("\n=== Indexing Retry Summary ===")
    print(f"  Status: {data.get('status', 'unknown')}")
    print(f"  Processed: {data.get('processed', 0)}")
    print("==============================")


def execute_migrate() -> None:
    from src.core.database.migrations import run_database_migrations

    db_manager = DatabaseManager()
    try:
        applied = run_database_migrations(db_manager)
        if applied:
            print(f"Applied database migrations: {', '.join(map(str, applied))}")
        else:
            print("Database schema is up to date.")
    finally:
        db_manager.close()


def execute_evaluate_search(owner_id: str, cases_path: str, limit: int = 5, output_path: str = None) -> None:
    from src.retrieval.evaluation import evaluate_search, load_evaluation_cases

    owner_token = activate_owner_context(owner_id)
    db_manager = DatabaseManager()
    try:
        searcher = create_wiki_searcher(db_manager, get_embedding_service())
        cases = load_evaluation_cases(cases_path)
        report = evaluate_search(cases, searcher.search, limit=limit)
        rendered = json.dumps(report, ensure_ascii=False, indent=2)
        if output_path:
            Path(output_path).write_text(rendered + "\n", encoding="utf-8")
        print(rendered)
    finally:
        db_manager.close()
        deactivate_owner_context(owner_token)


def execute_run_blind_search(owner_id: str, queries_path: str, output_path: str, limit: int = 5) -> None:
    from src.retrieval.evaluation import load_blind_queries, run_blind_search

    owner_token = activate_owner_context(owner_id)
    db_manager = DatabaseManager()
    try:
        searcher = create_wiki_searcher(db_manager, get_embedding_service())
        report = run_blind_search(load_blind_queries(queries_path), searcher.search, limit)
        rendered = json.dumps(report, ensure_ascii=False, indent=2)
        Path(output_path).write_text(rendered + "\n", encoding="utf-8")
        print(rendered)
    finally:
        db_manager.close()
        deactivate_owner_context(owner_token)


def execute_score_blind_search(queries_path: str, predictions_path: str, answers_path: str, dev_cases_path: str, gates_path: str, output_path: str = None) -> None:
    from src.retrieval.evaluation import load_blind_queries, load_evaluation_cases, score_blind_predictions

    predictions = json.loads(Path(predictions_path).read_text(encoding="utf-8"))
    answers = json.loads(Path(answers_path).read_text(encoding="utf-8"))
    gates = json.loads(Path(gates_path).read_text(encoding="utf-8"))["minimums"]
    report = score_blind_predictions(
        load_blind_queries(queries_path), predictions, answers,
        load_evaluation_cases(dev_cases_path), gates,
    )
    rendered = json.dumps(report, ensure_ascii=False, indent=2)
    if output_path:
        Path(output_path).write_text(rendered + "\n", encoding="utf-8")
    print(rendered)


def execute_check_direct_regression(baseline_path: str, candidate_path: str, gates_path: str) -> None:
    from src.retrieval.evaluation import compare_direct_regression

    baseline = json.loads(Path(baseline_path).read_text(encoding="utf-8"))
    candidate = json.loads(Path(candidate_path).read_text(encoding="utf-8"))
    gates = json.loads(Path(gates_path).read_text(encoding="utf-8"))
    report = compare_direct_regression(
        baseline["summary"], candidate["summary"], gates["direct_maximum_regressions"],
    )
    rendered = json.dumps(report, ensure_ascii=False, indent=2)
    print(rendered)
    if not report["passed"]:
        sys.exit(1)


def execute_generate_blind_search(owner_id: str, dev_cases: str, queries_out: str, answers_out: str, seed: str, answer_cases: int, no_answer_cases: int, exclude_answers: list, query_types: str) -> None:
    from src.retrieval.blind_dataset import generate_blind_dataset

    owner_token = activate_owner_context(owner_id)
    db_manager = DatabaseManager()
    try:
        result = generate_blind_dataset(
            db_manager, dev_cases, queries_out, answers_out,
            seed=seed, answer_cases=answer_cases, no_answer_cases=no_answer_cases,
            exclude_answer_files=exclude_answers,
            query_types=[item.strip() for item in query_types.split(",") if item.strip()],
        )
        print(json.dumps(result, ensure_ascii=False, indent=2))
    finally:
        db_manager.close()
        deactivate_owner_context(owner_token)


def execute_diagnose_search_stages(owner_id: str, queries_path: str, answers_path: str, output_path: str = None) -> None:
    from src.retrieval.diagnostics import diagnose_retrieval_stages
    from src.retrieval.evaluation import load_blind_queries

    owner_token = activate_owner_context(owner_id)
    db_manager = DatabaseManager()
    try:
        searcher = create_wiki_searcher(db_manager, get_embedding_service())
        answers = json.loads(Path(answers_path).read_text(encoding="utf-8"))
        report = diagnose_retrieval_stages(load_blind_queries(queries_path), answers, searcher)
        rendered = json.dumps(report, ensure_ascii=False, indent=2)
        if output_path:
            Path(output_path).write_text(rendered + "\n", encoding="utf-8")
        print(rendered)
    finally:
        db_manager.close()
        deactivate_owner_context(owner_token)
