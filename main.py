import argparse
from src.indexing.composition import create_wiki_indexer
from src.retrieval.composition import create_wiki_searcher
from src.cli.runner import (
    execute_index,
    execute_search,
    execute_retry_indexing,
    execute_migrate,
    execute_evaluate_search,
    execute_run_blind_search,
    execute_score_blind_search,
    execute_check_direct_regression,
    execute_generate_blind_search,
    execute_diagnose_search_stages,
)


def build_parser() -> argparse.ArgumentParser:
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
    
    return parser


def main():
    parser = build_parser()
    args = parser.parse_args()
    
    if args.command == "index":
        execute_index(args.owner_id)
    elif args.command == "search":
        execute_search(args.query, args.owner_id, limit=args.limit)
    elif args.command == "retry-indexing":
        execute_retry_indexing(limit=args.limit, force=args.force)
    elif args.command == "migrate":
        execute_migrate()
    elif args.command == "evaluate-search":
        execute_evaluate_search(args.owner_id, args.cases, limit=args.limit, output_path=args.output)
    elif args.command == "run-blind-search":
        execute_run_blind_search(args.owner_id, args.queries, args.output, limit=args.limit)
    elif args.command == "score-blind-search":
        execute_score_blind_search(args.queries, args.predictions, args.answers, args.development_cases, args.gates, output_path=args.output)
    elif args.command == "generate-blind-search":
        execute_generate_blind_search(
            args.owner_id, args.development_cases, args.queries_output, args.answers_output,
            args.seed, args.answer_cases, args.no_answer_cases, args.exclude_answers, args.query_types,
        )
    elif args.command == "check-direct-regression":
        execute_check_direct_regression(args.baseline, args.candidate, args.gates)
    elif args.command == "diagnose-search-stages":
        execute_diagnose_search_stages(args.owner_id, args.queries, args.answers, output_path=args.output)


if __name__ == "__main__":
    main()
