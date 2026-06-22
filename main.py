import argparse
import sys
from src.infrastructure.database import DatabaseManager
from src.domain.indexing.service import WikiIndexer
from src.domain.retrieval.service import WikiSearcher
from src.core.config import EMBEDDING_DIM, EMBEDDING_PROVIDER, WIKI_DIR

def get_embedding_service():
    """
    설정된 EMBEDDING_PROVIDER에 따라 알맞은 임베딩 서비스 구현체를 리턴합니다.
    """
    if EMBEDDING_PROVIDER == "openai":
        from src.domain.indexing.embedding import OpenAIEmbeddingService
        return OpenAIEmbeddingService(dimension=EMBEDDING_DIM)
    elif EMBEDDING_PROVIDER == "bge-m3":
        from src.domain.indexing.embedding import BGEM3EmbeddingService
        return BGEM3EmbeddingService()
    else:
        # 기본값 및 fake 공급자
        from src.domain.indexing.embedding import FakeEmbeddingService
        return FakeEmbeddingService(dimension=EMBEDDING_DIM)

def cmd_index(args):
    db_manager = DatabaseManager()
    embedding_service = get_embedding_service()
    
    # 현재 디렉토리가 지식베이스 루트 디렉토리입니다.
    indexer = WikiIndexer(root_dir=WIKI_DIR, db_manager=db_manager, embedding_service=embedding_service)
    
    try:
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

def cmd_search(args):
    db_manager = DatabaseManager()
    embedding_service = get_embedding_service()
    searcher = WikiSearcher(db_manager=db_manager, embedding_service=embedding_service)
    
    try:
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

def main():
    parser = argparse.ArgumentParser(description="LLM-Wiki Indexer & Searcher CLI")
    subparsers = parser.add_subparsers(dest="command", required=True, help="Subcommands")
    
    # index 서브커맨드
    subparsers.add_parser("index", help="Scan qa/ and topics/ directories and index files to PostgreSQL")
    
    # search 서브커맨드
    search_parser = subparsers.add_parser("search", help="Perform vector similarity search on indexed documents")
    search_parser.add_argument("query", type=str, help="Search query")
    search_parser.add_argument("--limit", type=int, default=5, help="Number of results to return")
    
    args = parser.parse_args()
    
    if args.command == "index":
        cmd_index(args)
    elif args.command == "search":
        cmd_search(args)

if __name__ == "__main__":
    main()
