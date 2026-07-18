import argparse
import json
import sys
from src.core.database.factory import DatabaseManager
from src.indexing.composition import create_wiki_indexer
from src.retrieval.application.service import WikiSearcher
from src.core.config import EMBEDDING_DIM, EMBEDDING_PROVIDER
from src.core.config import current_user_config


def activate_owner_context(owner_id: str):
    from src.settings.service import UserSettingsService

    service = UserSettingsService()
    try:
        stored_config = service.get_runtime_config(owner_id)
    finally:
        service.db_manager.close()
    if not stored_config:
        raise RuntimeError(f"사용자 {owner_id}의 OpenAI/S3 설정이 DB에 없습니다.")
    return current_user_config.set({
        "api_key": f"cli:{owner_id}",
        "user_id": owner_id,
        **stored_config,
    })

def get_embedding_service():
    """
    설정된 EMBEDDING_PROVIDER에 따라 알맞은 임베딩 서비스 구현체를 리턴합니다.
    """
    if EMBEDDING_PROVIDER == "openai":
        from src.indexing.domain.embedding import OpenAIEmbeddingService
        return OpenAIEmbeddingService(dimension=EMBEDDING_DIM)
    elif EMBEDDING_PROVIDER == "bge-m3":
        from src.indexing.domain.embedding import BGEM3EmbeddingService
        return BGEM3EmbeddingService()
    else:
        # 기본값 및 fake 공급자
        from src.indexing.domain.embedding import FakeEmbeddingService
        return FakeEmbeddingService(dimension=EMBEDDING_DIM)

def cmd_index(args):
    owner_token = activate_owner_context(args.owner_id)
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
        current_user_config.reset(owner_token)

def cmd_search(args):
    owner_token = activate_owner_context(args.owner_id)
    db_manager = DatabaseManager()
    try:
        embedding_service = get_embedding_service()
        searcher = WikiSearcher(db_manager=db_manager, embedding_service=embedding_service)
        results = searcher.search(args.query, limit=args.limit)
        if not results:
            print("No matching documents found.")
            return
            
        print(f"\nFound {len(results)} matching document(s) for query: '{args.query}' (Provider: {EMBEDDING_PROVIDER})\n")
        for i, doc in enumerate(results, 1):
            print(f"{i}. [{doc['title']}] ({doc['file_path']})")
            print(f"   Similarity Score: {doc['similarity']:.4f} | Type: {doc['doc_type']}")
            if doc['description']:
                print(f"   Description: {doc['description']}")
            if doc['tags']:
                print(f"   Tags: {', '.join(doc['tags'])}")
            
            print("   " + "-" * 40)
            # 본문의 첫 3줄 미리보기 출력
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
        current_user_config.reset(owner_token)

def cmd_retry_indexing(args):
    """스케줄러에서 호출할 수 있는 실패 인덱싱 큐 일괄 재처리 명령입니다."""
    from src.api.agent_tool import retry_wiki_indexing

    response = json.loads(retry_wiki_indexing(limit=args.limit, force=args.force))
    if not response.get("success"):
        print(response.get("message", "Failed to retry indexing"), file=sys.stderr)
        sys.exit(1)

    data = response.get("data") or {}
    print("\n=== Indexing Retry Summary ===")
    print(f"  Status: {data.get('status', 'unknown')}")
    print(f"  Processed: {data.get('processed', 0)}")
    print("==============================")

def cmd_migrate(_args):
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


def cmd_evaluate_search(args):
    from pathlib import Path
    from src.retrieval.evaluation import evaluate_search, load_evaluation_cases

    owner_token = activate_owner_context(args.owner_id)
    db_manager = DatabaseManager()
    try:
        searcher = WikiSearcher(
            db_manager=db_manager,
            embedding_service=get_embedding_service(),
        )
        cases = load_evaluation_cases(args.cases)
        report = evaluate_search(cases, searcher.search, limit=args.limit)
        rendered = json.dumps(report, ensure_ascii=False, indent=2)
        if args.output:
            Path(args.output).write_text(rendered + "\n", encoding="utf-8")
        print(rendered)
    finally:
        db_manager.close()
        current_user_config.reset(owner_token)


def cmd_run_blind_search(args):
    from pathlib import Path
    from src.retrieval.evaluation import load_blind_queries, run_blind_search

    owner_token = activate_owner_context(args.owner_id)
    db_manager = DatabaseManager()
    try:
        searcher = WikiSearcher(db_manager=db_manager, embedding_service=get_embedding_service())
        report = run_blind_search(load_blind_queries(args.queries), searcher.search, args.limit)
        rendered = json.dumps(report, ensure_ascii=False, indent=2)
        Path(args.output).write_text(rendered + "\n", encoding="utf-8")
        print(rendered)
    finally:
        db_manager.close()
        current_user_config.reset(owner_token)


def cmd_score_blind_search(args):
    from pathlib import Path
    from src.retrieval.evaluation import load_blind_queries, load_evaluation_cases, score_blind_predictions

    predictions = json.loads(Path(args.predictions).read_text(encoding="utf-8"))
    answers = json.loads(Path(args.answers).read_text(encoding="utf-8"))
    gates = json.loads(Path(args.gates).read_text(encoding="utf-8"))["minimums"]
    report = score_blind_predictions(
        load_blind_queries(args.queries), predictions, answers,
        load_evaluation_cases(args.development_cases), gates,
    )
    rendered = json.dumps(report, ensure_ascii=False, indent=2)
    if args.output:
        Path(args.output).write_text(rendered + "\n", encoding="utf-8")
    print(rendered)


def cmd_check_direct_regression(args):
    from pathlib import Path
    from src.retrieval.evaluation import compare_direct_regression

    baseline = json.loads(Path(args.baseline).read_text(encoding="utf-8"))
    candidate = json.loads(Path(args.candidate).read_text(encoding="utf-8"))
    gates = json.loads(Path(args.gates).read_text(encoding="utf-8"))
    report = compare_direct_regression(
        baseline["summary"], candidate["summary"], gates["direct_maximum_regressions"],
    )
    rendered = json.dumps(report, ensure_ascii=False, indent=2)
    print(rendered)
    if not report["passed"]:
        sys.exit(1)


def cmd_generate_blind_search(args):
    from src.retrieval.blind_dataset import generate_blind_dataset

    owner_token = activate_owner_context(args.owner_id)
    db_manager = DatabaseManager()
    try:
        result = generate_blind_dataset(
            db_manager, args.development_cases, args.queries_output, args.answers_output,
            seed=args.seed, answer_cases=args.answer_cases, no_answer_cases=args.no_answer_cases,
            exclude_answer_files=args.exclude_answers,
            query_types=[item.strip() for item in args.query_types.split(",") if item.strip()],
        )
        print(json.dumps(result, ensure_ascii=False, indent=2))
    finally:
        db_manager.close()
        current_user_config.reset(owner_token)


def cmd_diagnose_search_stages(args):
    from pathlib import Path
    from src.retrieval.diagnostics import diagnose_retrieval_stages
    from src.retrieval.evaluation import load_blind_queries

    owner_token = activate_owner_context(args.owner_id)
    db_manager = DatabaseManager()
    try:
        searcher = WikiSearcher(db_manager=db_manager, embedding_service=get_embedding_service())
        answers = json.loads(Path(args.answers).read_text(encoding="utf-8"))
        report = diagnose_retrieval_stages(load_blind_queries(args.queries), answers, searcher)
        rendered = json.dumps(report, ensure_ascii=False, indent=2)
        if args.output:
            Path(args.output).write_text(rendered + "\n", encoding="utf-8")
        print(rendered)
    finally:
        db_manager.close()
        current_user_config.reset(owner_token)

def main():
    parser = argparse.ArgumentParser(description="LLM-Wiki Indexer & Searcher CLI")
    subparsers = parser.add_subparsers(dest="command", required=True, help="Subcommands")
    
    # index 서브커맨드
    index_parser = subparsers.add_parser("index", help="Scan the owner's S3 knowledge objects and index them")
    index_parser.add_argument("--owner-id", required=True, help="Owner whose DB-backed S3/OpenAI settings are used")
    
    # search 서브커맨드
    search_parser = subparsers.add_parser("search", help="Perform vector similarity search on indexed documents")
    search_parser.add_argument("query", type=str, help="Search query")
    search_parser.add_argument("--limit", type=int, default=5, help="Number of results to return")
    search_parser.add_argument("--owner-id", required=True, help="Owner whose DB-backed S3/OpenAI settings are used")

    retry_parser = subparsers.add_parser(
        "retry-indexing",
        help="Retry queued indexing jobs (intended for cron/Kubernetes CronJob)",
    )
    retry_parser.add_argument("--limit", type=int, default=100, help="Maximum jobs per run")
    retry_parser.add_argument(
        "--force",
        action="store_true",
        help="Ignore retry schedule and maximum attempts",
    )

    subparsers.add_parser(
        "migrate",
        help="Apply all pending PostgreSQL schema migrations",
    )

    evaluation_parser = subparsers.add_parser(
        "evaluate-search",
        help="Evaluate search quality against a golden query set",
    )
    evaluation_parser.add_argument("--owner-id", required=True)
    evaluation_parser.add_argument("--cases", default="search_quality_development.json")
    evaluation_parser.add_argument("--limit", type=int, default=5)
    evaluation_parser.add_argument("--output")

    blind_run_parser = subparsers.add_parser("run-blind-search", help="Run blind queries without access to answers")
    blind_run_parser.add_argument("--owner-id", required=True)
    blind_run_parser.add_argument("--queries", required=True)
    blind_run_parser.add_argument("--limit", type=int, default=5)
    blind_run_parser.add_argument("--output", required=True)

    blind_score_parser = subparsers.add_parser("score-blind-search", help="Score frozen blind predictions")
    blind_score_parser.add_argument("--queries", required=True)
    blind_score_parser.add_argument("--predictions", required=True)
    blind_score_parser.add_argument("--answers", required=True)
    blind_score_parser.add_argument("--development-cases", default="search_quality_development.json")
    blind_score_parser.add_argument("--gates", default="search_quality_gates.json")
    blind_score_parser.add_argument("--output")

    blind_generate_parser = subparsers.add_parser("generate-blind-search", help="Generate a private prospective blind holdout set")
    blind_generate_parser.add_argument("--owner-id", required=True)
    blind_generate_parser.add_argument("--development-cases", default="search_quality_development.json")
    blind_generate_parser.add_argument("--queries-output", required=True)
    blind_generate_parser.add_argument("--answers-output", required=True)
    blind_generate_parser.add_argument("--seed", default="blind-v1")
    blind_generate_parser.add_argument("--answer-cases", type=int, default=40)
    blind_generate_parser.add_argument("--no-answer-cases", type=int, default=10)
    blind_generate_parser.add_argument("--exclude-answers", action="append", default=[])
    blind_generate_parser.add_argument("--query-types", default="exact,semantic,cross-language,acronym,mixed-language")

    regression_parser = subparsers.add_parser(
        "check-direct-regression",
        help="Reject an ontology candidate report when direct search metrics regress",
    )
    regression_parser.add_argument("--baseline", default="tests/direct_search_baseline.json")
    regression_parser.add_argument("--candidate", required=True)
    regression_parser.add_argument("--gates", default="tests/ontology_quality_gates.json")

    diagnose_parser = subparsers.add_parser("diagnose-search-stages", help="Trace expected documents through retrieval stages")
    diagnose_parser.add_argument("--owner-id", required=True)
    diagnose_parser.add_argument("--queries", required=True)
    diagnose_parser.add_argument("--answers", required=True)
    diagnose_parser.add_argument("--output")
    
    args = parser.parse_args()
    
    if args.command == "index":
        cmd_index(args)
    elif args.command == "search":
        cmd_search(args)
    elif args.command == "retry-indexing":
        cmd_retry_indexing(args)
    elif args.command == "migrate":
        cmd_migrate(args)
    elif args.command == "evaluate-search":
        cmd_evaluate_search(args)
    elif args.command == "run-blind-search":
        cmd_run_blind_search(args)
    elif args.command == "score-blind-search":
        cmd_score_blind_search(args)
    elif args.command == "generate-blind-search":
        cmd_generate_blind_search(args)
    elif args.command == "check-direct-regression":
        cmd_check_direct_regression(args)
    elif args.command == "diagnose-search-stages":
        cmd_diagnose_search_stages(args)

if __name__ == "__main__":
    main()
