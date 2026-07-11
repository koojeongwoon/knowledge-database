import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import List, Dict, Any, Tuple

from src.core.config import STORAGE_TYPE
from src.core.database.factory import DatabaseManager
from src.core.storage.factory import StorageManager
from src.indexing.domain.embedding import BaseEmbeddingService
from src.wiki.domain.parser import parse_markdown_content

# 이 임계치를 초과하는 파일이 변경되었을 때 병렬 처리로 전환
PARALLEL_THRESHOLD = 10
PARALLEL_WORKERS = 4


from src.indexing.infrastructure.repository import IndexingRepository

class WikiIndexer:
    def __init__(self, root_dir: str, db_manager: DatabaseManager, embedding_service: BaseEmbeddingService):
        self.root_dir = root_dir
        self.db_manager = db_manager
        self.repository = IndexingRepository(db_manager)
        self.embedding_service = embedding_service
        self.target_dirs = ["qa", "topics", "assets", "attachments"]
        self.storage = StorageManager()

    def _get_local_files(self) -> List[str]:
        """
        qa/ 및 topics/ 디렉토리 내의 모든 마크다운 파일들의 상대경로 목록을 가져옵니다.
        """
        local_files = []
        for target in self.target_dirs:
            # StorageManager의 list_files를 사용하여 물리/가상 스토리지의 파일 스캔
            files = self.storage.list_files(target, "*.md")
            local_files.extend(files)
        return local_files

    def _process_single_file(self, rel_path: str, is_new: bool, db_manager: DatabaseManager) -> str:
        """
        단일 파일의 파싱 → 임베딩 → DB 적재를 처리합니다.
        비즈니스 연산(Edge/Chunk)은 도메인 모델에 위임합니다.
        Returns: 'created' | 'updated'
        """
        from src.wiki.domain.parser import extract_wiki_links, split_markdown_by_headers, chunk_text
        from src.indexing.domain.model import Edge, Chunk
        from src.indexing.infrastructure.repository import IndexingRepository
        repo = IndexingRepository(db_manager)

        content = self.storage.read_text(rel_path)
        parsed_data = parse_markdown_content(content, rel_path)
        content_hash = parsed_data["content_hash"]

        fm = parsed_data["frontmatter"]
        doc_type = fm.get("type")

        if not doc_type:
            if rel_path.startswith("qa"):
                doc_type = "QAJournal"
            elif rel_path.startswith("topics"):
                doc_type = "TopicSummary"
            else:
                doc_type = "Unknown"

        title = fm.get("title") or os.path.splitext(os.path.basename(rel_path))[0]
        description = fm.get("description", "")
        tags = fm.get("tags", [])

        action_name = "Indexing new" if is_new else "Updating modified"
        print(f"[+] {action_name} file: {rel_path}")

        # 중복 등록 방지 및 데이터 무결성을 위해 기존 저장된 문서 청크와 엣지 삭제
        repo.delete_document(rel_path)

        # 본문 내 [[WikiLink]] 추출하여 엣지(관계) 저장
        wiki_links = extract_wiki_links(parsed_data["body"])
        source_meta = {"source_path": fm.get("source_path"), "type": doc_type}
        
        for target_topic in wiki_links:
            # 1. 엣지 가중치 결정을 도메인 모델(Edge)에 완벽 위임
            t_key = target_topic.lower()
            target_meta = getattr(self, "topic_metadata", {}).get(t_key)
            
            edge = Edge.create_with_4signal(
                source_path=rel_path,
                target_topic=target_topic,
                source_meta=source_meta,
                target_meta=target_meta,
                custom_relations=fm.get("custom_relations", [])
            )
            repo.insert_edge(edge.source_path, edge.target_topic, edge.weight)

        # 청크 수집 (임베딩 전 단계)
        parent_chunks = split_markdown_by_headers(parsed_data["body"])

        pending_chunks = []
        embedding_texts = []
        chunk_index = 0

        for parent in parent_chunks:
            header = parent["header"]
            parent_txt = parent["content"]
            chunk_title = f"{title} > {header}" if header != "Intro" else title

            child_txts = chunk_text(parent_txt, max_chars=300, overlap=50)

            for child_txt in child_txts:
                # 2. 청크 값 객체 생성하여 임베딩 텍스트 빌드 위임
                chunk = Chunk(
                    file_path=rel_path,
                    chunk_index=chunk_index,
                    doc_type=doc_type,
                    title=chunk_title,
                    description=description,
                    tags=tags,
                    content=child_txt,
                    parent_content=parent_txt,
                    raw_frontmatter=fm,
                    content_hash=content_hash
                )
                embedding_texts.append(chunk.to_embedding_text())
                pending_chunks.append(chunk)
                chunk_index += 1

        # 배치 임베딩 (100개 단위 청킹)
        embeddings = self.embedding_service.embed_batch(embedding_texts)

        db_chunks = []
        for chunk, embedding in zip(pending_chunks, embeddings):
            c_dict = chunk.to_dict()
            c_dict["embedding"] = embedding
            db_chunks.append(c_dict)

        # 배치 DB INSERT (50개 단위 청킹)
        repo.upsert_document_chunks_batch(db_chunks)

        return "created" if is_new else "updated"

    def run_indexing(self) -> Dict[str, Any]:
        """
        증분 인덱싱 파이프라인을 실행합니다.
        - 로컬 스캔 결과와 DB 데이터를 비교하여 변경된 건만 임베딩/업서트
        - 로컬에서 삭제된 파일은 DB에서도 삭제
        - 새로 쓰인 파일은 wiki_links를 추출하여 엣지 적재
        - 문서를 부모/자식 단위로 이중 청킹하여 개별 임베딩 후 적재
        - 처리 대상이 PARALLEL_THRESHOLD를 초과하면 병렬 처리로 자동 전환
        """
        print("Starting LLM-Wiki incremental indexing...")
        
        # 0. 이미지 전처리 프로세스 실행 (사이드카 캐싱)
        # 0. 이미지 전처리 프로세스 실행 (로컬 파일 시스템 전용 사이드카 캐싱)
        if STORAGE_TYPE == "local":
            try:
                from src.media.application.processor import ImageProcessor
                image_processor = ImageProcessor(root_dir=self.root_dir)
                image_stats = image_processor.process_images()
                print(f"[+] Image preprocessing completed. Stats: {image_stats}")
            except Exception as e:
                print(f"[✗] Warning: Image preprocessing failed: {e}")
        else:
            print("[~] Storage is set to S3/R2. Skipping local image preprocessing.")
        
        # 1. DB 초기화 (테이블 및 인덱스 생성)
        self.repository.initialize_db()
        
        # 2. 로컬 파일 목록 및 DB의 파일 해시 맵 획득
        local_files = self._get_local_files()
        
        # 2-1. 로컬 메타데이터 캐시 구축 (가중치 계산용)
        self.topic_metadata = {}
        for f in local_files:
            try:
                content = self.storage.read_text(f)
                parsed_data = parse_markdown_content(content, f)
                fm = parsed_data.get("frontmatter", {})
                title = fm.get("title", "")
                s_path = fm.get("source_path")
                t_type = fm.get("type")
                
                t_slug = os.path.splitext(os.path.basename(f))[0].lower()
                metadata = {"source_path": s_path, "type": t_type}
                
                self.topic_metadata[t_slug] = metadata
                if title:
                    self.topic_metadata[title.lower()] = metadata
            except Exception:
                pass
                
        db_hashes = self.repository.get_all_file_hashes()
        
        stats = {
            "created": 0,
            "updated": 0,
            "deleted": 0,
            "skipped": 0
        }
        
        local_files_set = set(local_files)
        db_files_set = set(db_hashes.keys())
        
        # 3. 로컬에서 제거된 인덱스 파일 DB에서 삭제
        to_delete = db_files_set - local_files_set
        for rel_path in to_delete:
            print(f"[-] Deleting indexed file (removed locally): {rel_path}")
            self.repository.delete_document(rel_path)
            stats["deleted"] += 1
            
        # 4. 변경 대상 파일 필터링
        targets: List[Tuple[str, bool]] = []  # (rel_path, is_new)
        for rel_path in local_files:
            content = self.storage.read_text(rel_path)
            parsed_data = parse_markdown_content(content, rel_path)
            content_hash = parsed_data["content_hash"]

            is_new = rel_path not in db_hashes
            is_modified = not is_new and db_hashes[rel_path] != content_hash

            if is_new or is_modified:
                targets.append((rel_path, is_new))
            else:
                stats["skipped"] += 1

        # 5. 변경 대상이 없으면 조기 종료
        if not targets:
            print(f"Indexing completed. Stats: {stats}")
            return stats

        # 6. 임계치 기준으로 직렬/병렬 결정
        use_parallel = len(targets) > PARALLEL_THRESHOLD

        if use_parallel:
            print(f"[⚡] {len(targets)} files to process (> {PARALLEL_THRESHOLD}) — switching to parallel mode ({PARALLEL_WORKERS} workers)")
            self._process_parallel(targets, stats)
        else:
            print(f"[→] {len(targets)} files to process — sequential mode")
            self._process_sequential(targets, stats)
                
        print(f"Indexing completed. Stats: {stats}")
        return stats

    def _process_sequential(self, targets: List[Tuple[str, bool]], stats: Dict[str, Any]):
        """변경 대상 파일들을 직렬로 처리합니다. 기존 db_manager를 그대로 사용합니다."""
        for rel_path, is_new in targets:
            result = self._process_single_file(rel_path, is_new, self.db_manager)
            stats[result] += 1

    def _process_parallel(self, targets: List[Tuple[str, bool]], stats: Dict[str, Any]):
        """
        변경 대상 파일들을 ThreadPoolExecutor로 병렬 처리합니다.
        각 워커가 독립 DatabaseManager를 생성하여 커넥션 충돌을 방지합니다.
        """
        errors = []

        def _worker(rel_path: str, is_new: bool) -> Tuple[str, str]:
            """워커 함수: 독립 DB 커넥션으로 단일 파일 처리 후 결과 반환"""
            worker_db = DatabaseManager()
            try:
                result = self._process_single_file(rel_path, is_new, worker_db)
                return (rel_path, result)
            except Exception as e:
                return (rel_path, f"error:{e}")
            finally:
                worker_db.close()

        with ThreadPoolExecutor(max_workers=PARALLEL_WORKERS) as executor:
            futures = {
                executor.submit(_worker, rel_path, is_new): rel_path
                for rel_path, is_new in targets
            }

            for future in as_completed(futures):
                rel_path, result = future.result()
                if result.startswith("error:"):
                    error_msg = result[6:]
                    print(f"[✗] Error processing {rel_path}: {error_msg}")
                    errors.append((rel_path, error_msg))
                else:
                    stats[result] += 1

        if errors:
            print(f"[!] {len(errors)} file(s) failed during parallel indexing:")
            for path, msg in errors:
                print(f"    - {path}: {msg}")
