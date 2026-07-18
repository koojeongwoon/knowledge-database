import posixpath
from typing import Any, Callable, Dict, List, Optional, Tuple

from src.core.database.base import BaseDatabaseManager
from src.core.storage.base import BaseStorageManager
from src.indexing.application.expansion_executor import DocumentExpansionExecutor
from src.indexing.application.file_executor import FileIndexingExecutor
from src.indexing.application.inventory_collector import IndexingInventoryCollector
from src.indexing.domain.chunk_plan import materialize_chunk_records, plan_document_chunks
from src.indexing.domain.document_plan import plan_document_edges, resolve_document_metadata
from src.indexing.domain.embedding import BaseEmbeddingService
from src.indexing.domain.events import IndexingEvent, IndexingEventKind, IndexingObserver
from src.indexing.domain.execution import FileIndexingOutcome, IndexingStats
from src.indexing.domain.ontology import OntologyShadowPort
from src.indexing.domain.plan import plan_indexing_changes
from src.indexing.domain.repository import BaseIndexingRepository
from src.wiki.domain.parser import parse_markdown_content

# 이 임계치를 초과하는 파일이 변경되었을 때 병렬 처리로 전환
PARALLEL_THRESHOLD = 10

class WikiIndexer:
    def __init__(
        self,
        db_manager: BaseDatabaseManager,
        embedding_service: BaseEmbeddingService,
        *,
        storage: BaseStorageManager,
        repository_factory: Callable[[BaseDatabaseManager], BaseIndexingRepository],
        file_executor: FileIndexingExecutor,
        expansion_executor: DocumentExpansionExecutor,
        inventory_collector: IndexingInventoryCollector,
        ontology_shadow_factory: Callable[[BaseDatabaseManager], OntologyShadowPort],
        observer: IndexingObserver,
    ):
        self.db_manager = db_manager
        self.repository_factory = repository_factory
        self.file_executor = file_executor
        self.repository = repository_factory(db_manager)
        self.embedding_service = embedding_service
        self.target_dirs = ["qa", "topics", "assets", "attachments"]
        self.storage = storage
        self.expansion_executor = expansion_executor
        self.inventory_collector = inventory_collector
        self.ontology_shadow_factory = ontology_shadow_factory
        self.observer = observer


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

    def _normalize_target_files(self, file_paths: List[str]) -> List[str]:
        """인덱싱 요청 경로를 스토리지 기준의 안전한 상대 경로로 정규화합니다."""
        normalized = []
        seen = set()
        for file_path in file_paths:
            if not isinstance(file_path, str) or not file_path.strip():
                raise ValueError("인덱싱 대상 경로는 비어 있지 않은 문자열이어야 합니다.")

            rel_path = file_path.strip().replace("\\", "/")
            if posixpath.isabs(rel_path):
                raise ValueError(f"S3 객체 키는 절대 경로일 수 없습니다: {file_path}")
            rel_path = posixpath.normpath(rel_path).replace("\\", "/")

            top_level = rel_path.split("/", 1)[0]
            if (
                rel_path in ("", ".")
                or rel_path == ".."
                or rel_path.startswith("../")
                or not rel_path.lower().endswith(".md")
                or top_level not in self.target_dirs
            ):
                raise ValueError(f"지원하지 않는 인덱싱 대상 경로입니다: {file_path}")

            if rel_path not in seen:
                normalized.append(rel_path)
                seen.add(rel_path)
        return normalized

    def _process_single_file(
        self,
        rel_path: str,
        is_new: bool,
        db_manager: BaseDatabaseManager,
    ) -> FileIndexingOutcome:
        """
        단일 파일의 파싱 → 임베딩 → DB 적재를 처리합니다.
        비즈니스 연산(Edge/Chunk)은 도메인 모델에 위임합니다.
        Returns: the successful file indexing outcome.
        """
        from src.wiki.domain.parser import extract_wiki_links, split_markdown_by_headers, chunk_text
        repo = self.repository_factory(db_manager)

        content = self.storage.read_text(rel_path)
        parsed_data = parse_markdown_content(content, rel_path)
        content_hash = parsed_data["content_hash"]

        fm = parsed_data["frontmatter"]
        metadata = resolve_document_metadata(rel_path, fm)
        doc_type = metadata.doc_type
        title = metadata.title
        description = metadata.description
        tags = metadata.tags

        action_name = "Indexing new" if is_new else "Updating modified"
        self.observer.emit(IndexingEvent(
            IndexingEventKind.FILE_STARTED,
            f"[+] {action_name} file: {rel_path}",
            file_path=rel_path,
            details=(("is_new", is_new),),
        ))

        # 삭제 전에 기존 임베딩을 읽어 동일한 청크의 임베딩을 재사용합니다.
        existing_chunks = repo.get_document_chunks(rel_path)
        existing_map = {c["content"]: c["embedding"] for c in existing_chunks if c.get("embedding")}

        # 본문 내 [[WikiLink]] 추출하여 엣지(관계) 저장
        wiki_links = extract_wiki_links(parsed_data["body"])
        source_meta = {"source_path": fm.get("source_path"), "type": doc_type}
        edges = plan_document_edges(
            source_path=rel_path,
            target_topics=wiki_links,
            source_metadata=source_meta,
            topic_metadata=getattr(self, "topic_metadata", {}),
            custom_relations=fm.get("custom_relations", []),
        )
        db_edges = [
            {
                "source_path": edge.source_path,
                "target_topic": edge.target_topic,
                "weight": edge.weight,
            }
            for edge in edges
        ]

        # 청크 수집 (임베딩 전 단계)
        parent_chunks = split_markdown_by_headers(parsed_data["body"])
        chunk_plan = plan_document_chunks(
            file_path=rel_path,
            doc_type=doc_type,
            title=title,
            description=description,
            tags=tags,
            raw_frontmatter=fm,
            content_hash=content_hash,
            parents=parent_chunks,
            existing_embeddings=existing_map,
            expansion_enabled=self.expansion_executor.enabled,
            chunker=lambda text: chunk_text(text, max_chars=300, overlap=50),
        )
        embedding_texts = list(chunk_plan.embedding_texts)
        expansion_tasks = chunk_plan.expansion_tasks

        embedding_texts = list(self.expansion_executor.expand(
            title=title,
            description=description,
            embedding_texts=embedding_texts,
            tasks=expansion_tasks,
        ))


        # 신규/변경된 텍스트들에 대해서만 배치 임베딩 요청
        embeddings = []
        if embedding_texts:
            self.observer.emit(IndexingEvent(
                IndexingEventKind.EMBEDDING_STARTED,
                f"[~] Embedding {len(embedding_texts)} new/modified chunks...",
                file_path=rel_path,
                details=(("chunk_count", len(embedding_texts)),),
            ))
            embeddings = self.embedding_service.embed_batch(embedding_texts)

        db_chunks = list(materialize_chunk_records(chunk_plan, embeddings))

        # 임베딩 준비가 모두 끝난 뒤 청크와 엣지를 하나의 트랜잭션으로 교체합니다.
        # 이 단계 이전에 실패하면 기존 검색 인덱스는 그대로 유지됩니다.
        repo.replace_document(rel_path, db_chunks, db_edges)

        # Ontology is a best-effort side path after the direct transaction has
        # succeeded. Its output is never added to direct chunks, edges, or ranks.
        self._process_ontology_shadow(rel_path, fm, content_hash, db_manager)

        return (
            FileIndexingOutcome.CREATED
            if is_new
            else FileIndexingOutcome.UPDATED
        )

    def _process_ontology_shadow(
        self,
        rel_path: str,
        frontmatter: Dict[str, Any],
        content_hash: str,
        db_manager: BaseDatabaseManager,
    ) -> None:
        try:
            service = self.ontology_shadow_factory(db_manager)
            outcome = service.process_safely(rel_path, frontmatter, content_hash)
            if outcome.enabled:
                self.observer.emit(IndexingEvent(
                    IndexingEventKind.ONTOLOGY_SHADOW,
                    f"[ontology-shadow] {rel_path}: status={outcome.status} "
                    f"concepts={outcome.concept_count} relations={outcome.relation_count}",
                    file_path=rel_path,
                ))
        except Exception as exc:
            # Defense in depth: no ontology wiring error may change direct indexing.
            self.observer.emit(IndexingEvent(
                IndexingEventKind.WARNING,
                f"Warning: Ontology shadow failed for {rel_path}: {exc}",
                file_path=rel_path,
            ))

    def _delete_ontology_shadow(self, rel_path: str, db_manager: BaseDatabaseManager) -> None:
        try:
            service = self.ontology_shadow_factory(db_manager)
            service.delete_safely(rel_path)
        except Exception as exc:
            self.observer.emit(IndexingEvent(
                IndexingEventKind.WARNING,
                f"Warning: Ontology shadow delete failed for {rel_path}: {exc}",
                file_path=rel_path,
            ))



    def run_indexing(self, file_paths: Optional[List[str]] = None) -> IndexingStats:
        """
        증분 인덱싱 파이프라인을 실행합니다.
        - 로컬 스캔 결과와 DB 데이터를 비교하여 변경된 건만 임베딩/업서트
        - 로컬에서 삭제된 파일은 DB에서도 삭제
        - 새로 쓰인 파일은 wiki_links를 추출하여 엣지 적재
        - 문서를 부모/자식 단위로 이중 청킹하여 개별 임베딩 후 적재
        - 처리 대상이 PARALLEL_THRESHOLD를 초과하면 병렬 처리로 자동 전환
        """
        scoped = file_paths is not None
        requested_files = self._normalize_target_files(file_paths or []) if scoped else None
        self.observer.emit(IndexingEvent(
            IndexingEventKind.STARTED,
            "Starting LLM-Wiki incremental indexing...",
        ))
        
        # 1. DB 초기화 (테이블 및 인덱스 생성)
        self.repository.initialize_db()
        
        # 2. 로컬 파일 목록 및 DB의 파일 해시 맵 획득
        local_files = (
            [path for path in requested_files if self.storage.exists(path)]
            if scoped
            else self._get_local_files()
        )
        
        # 2-1. 파일을 한 번씩 파싱하여 불변 inventory를 구축합니다.
        inventory = self.inventory_collector.collect(local_files)
        local_hashes = inventory.local_hashes
        self.topic_metadata = inventory.edge_metadata

        # topic DB synchronization is best-effort and does not invalidate hashes.
        for command in inventory.topic_sync_commands:
            try:
                self.repository.upsert_topic(
                    command.topic_name,
                    command.category,
                    command.file_path,
                )
            except Exception as e:
                self.observer.emit(IndexingEvent(
                    IndexingEventKind.WARNING,
                    f"Warning: Failed to sync topic database for "
                    f"{command.file_path}: {e}",
                    file_path=command.file_path,
                ))

        db_hashes = (
            self.repository.get_file_hashes(requested_files)
            if scoped
            else self.repository.get_all_file_hashes()
        )
        
        plan = plan_indexing_changes(local_hashes, db_hashes)
        stats: IndexingStats = {
            "created": 0,
            "updated": 0,
            "deleted": 0,
            "skipped": len(plan.skipped),
        }

        # 3. 로컬에서 제거된 인덱스 파일 DB에서 삭제
        for rel_path in plan.deleted:
            self.observer.emit(IndexingEvent(
                IndexingEventKind.FILE_DELETED,
                f"[-] Deleting indexed file (removed locally): {rel_path}",
                file_path=rel_path,
            ))
            self.repository.delete_document(rel_path)
            self._delete_ontology_shadow(rel_path, self.db_manager)
            stats["deleted"] += 1

        # 4. 순수 도메인 계획에서 처리 대상 획득
        targets = list(plan.processing_targets)

        # 5. 변경 대상이 없으면 조기 종료
        if not targets:
            self._emit_completed(stats)
            return stats

        # 6. 임계치 기준으로 직렬/병렬 결정
        use_parallel = len(targets) > PARALLEL_THRESHOLD

        if use_parallel:
            self.observer.emit(IndexingEvent(
                IndexingEventKind.MODE_SELECTED,
                f"[parallel] {len(targets)} files to process (> {PARALLEL_THRESHOLD}); "
                f"using {self.file_executor.max_workers} workers",
                details=(("parallel", True), ("target_count", len(targets))),
            ))
            batch_result = self.file_executor.execute(
                targets=targets,
                process_file=self._process_single_file,
            )
            stats["created"] += batch_result.created_count
            stats["updated"] += batch_result.updated_count
            if batch_result.failures:
                for failure in batch_result.failures:
                    self.observer.emit(IndexingEvent(
                        IndexingEventKind.FILE_FAILED,
                        f"Indexing failed for {failure.file_path}: {failure.error_message}",
                        file_path=failure.file_path,
                    ))
        else:
            self.observer.emit(IndexingEvent(
                IndexingEventKind.MODE_SELECTED,
                f"[sequential] {len(targets)} files to process",
                details=(("parallel", False), ("target_count", len(targets))),
            ))
            self._process_sequential(targets, stats)
                
        self._emit_completed(stats)
        return stats

    def _emit_completed(self, stats: IndexingStats) -> None:
        self.observer.emit(IndexingEvent(
            IndexingEventKind.COMPLETED,
            f"Indexing completed. Stats: {stats}",
            details=tuple((key, value) for key, value in stats.items()),
        ))

    def _process_sequential(
        self,
        targets: List[Tuple[str, bool]],
        stats: IndexingStats,
    ) -> None:
        """변경 대상 파일들을 직렬로 처리합니다. 기존 db_manager를 그대로 사용합니다."""
        for rel_path, is_new in targets:
            result = self._process_single_file(rel_path, is_new, self.db_manager)
            stats[result.value] += 1
