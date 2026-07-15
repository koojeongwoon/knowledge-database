import posixpath
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import List, Dict, Any, Tuple, Optional

from src.core.database.factory import DatabaseManager
from src.core.storage.factory import StorageManager
from src.indexing.domain.embedding import BaseEmbeddingService
from src.wiki.domain.parser import parse_markdown_content

# 이 임계치를 초과하는 파일이 변경되었을 때 병렬 처리로 전환
PARALLEL_THRESHOLD = 10
PARALLEL_WORKERS = 4

from pydantic import BaseModel, Field

class SingleChunkExpansion(BaseModel):
    chunk_index: int = Field(description="The unique index of the chunk being expanded")
    questions: List[str] = Field(description="Exactly three natural user questions in Korean")
    keywords: List[str] = Field(description="Exactly five key search terms, keywords, synonyms, or translations")

class BatchExpansionResponse(BaseModel):
    expansions: List[SingleChunkExpansion] = Field(description="List of expansions for each given chunk")



from src.indexing.infrastructure.repository import IndexingRepository

class WikiIndexer:
    def __init__(self, root_dir: str, db_manager: DatabaseManager, embedding_service: BaseEmbeddingService):
        self.root_dir = root_dir
        self.db_manager = db_manager
        self.repository = IndexingRepository(db_manager)
        self.embedding_service = embedding_service
        self.target_dirs = ["qa", "topics", "assets", "attachments"]
        self.storage = StorageManager()
        
        # 문서 확장(Document Expansion)을 위한 OpenAI 클라이언트 초기화
        self.openai_client = None
        from src.core.config import DOCUMENT_EXPANSION_ENABLED
        if DOCUMENT_EXPANSION_ENABLED:
            try:
                from openai import OpenAI
                from src.core.config import current_user_config
                config = current_user_config.get() or {}
                api_key = config.get("openai_api_key")
                if api_key:
                    self.openai_client = OpenAI(api_key=api_key)
            except Exception as e:
                print(f"Warning: Failed to initialize OpenAI client for document expansion: {e}")

    def _generate_expansion_text(self, title: str, description: str, content: str) -> str:
        """
        LLM(gpt-4o-mini)을 호출하여 청크와 관련된 예상 질문 3개와 연관 키워드 5개를 생성합니다.
        """
        if not self.openai_client:
            return ""
        try:
            prompt = (
                "You are an AI assistant optimizing search indexes for a technical knowledge base.\n"
                "Analyze the given text snippet and generate:\n"
                "1. Three natural user questions (in Korean) that this text snippet directly answers.\n"
                "2. Five key search keywords/terms/synonyms/translations (both in Korean and English) relevant to this text.\n"
                "Keep the output extremely concise and return it as a plain list of questions and keywords.\n\n"
                f"Title: {title}\n"
                f"Description: {description}\n"
                f"Content:\n{content}\n"
            )
            response = self.openai_client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[{"role": "user", "content": prompt}],
                max_tokens=250,
                temperature=0.3
            )
            return response.choices[0].message.content.strip()
        except Exception as e:
            print(f"Warning: Failed to generate document expansion text: {e}")
            return ""

    def _generate_batch_expansion(self, title: str, description: str, batch_tasks: List[Tuple[int, str]]) -> List[Tuple[int, str]]:
        """
        Sends a mini-batch of chunks to OpenAI using Structured Outputs to generate questions/keywords.
        Returns list of (chunk_index, expansion_text)
        """
        if not self.openai_client:
            return []
        
        chunks_prompt = ""
        for idx, content in batch_tasks:
            chunks_prompt += f"=== [CHUNK INDEX {idx}] ===\n{content}\n\n"
            
        prompt = (
            "You are an AI assistant optimizing search indexes for a technical knowledge base.\n"
            f"Analyze the following list of separate text chunks from the document titled '{title}'.\n\n"
            f"Document Description: {description}\n\n"
            "For EACH chunk, generate:\n"
            "1. Three natural user questions (in Korean) that this specific chunk directly answers.\n"
            "2. Five key search keywords/terms/synonyms/translations (both in Korean and English) relevant to this chunk.\n\n"
            "You must respond in the specified Structured Output format.\n\n"
            "Here are the source chunks to analyze:\n"
            f"{chunks_prompt}"
        )
        
        try:
            response = self.openai_client.beta.chat.completions.parse(
                model="gpt-4o-mini",
                messages=[
                    {"role": "system", "content": "You are a technical search index optimizer."},
                    {"role": "user", "content": prompt}
                ],
                response_format=BatchExpansionResponse,
                temperature=0.2
            )
            
            parsed_response = response.choices[0].message.parsed
            results = []
            if parsed_response and parsed_response.expansions:
                for exp in parsed_response.expansions:
                    q_str = "\n".join(f"- {q}" for q in exp.questions)
                    k_str = ", ".join(exp.keywords)
                    expansion_text = f"[Expected Questions]\n{q_str}\n\n[Keywords]\n{k_str}"
                    results.append((exp.chunk_index, expansion_text))
            return results
        except Exception as e:
            print(f"Warning: Failed to generate batch expansion text via OpenAI Structured Outputs: {e}")
            return []


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

        title = fm.get("title") or posixpath.splitext(posixpath.basename(rel_path))[0]
        description = fm.get("description", "")
        tags = fm.get("tags", [])

        action_name = "Indexing new" if is_new else "Updating modified"
        print(f"[+] {action_name} file: {rel_path}")

        # 삭제 전에 기존 임베딩을 읽어 동일한 청크의 임베딩을 재사용합니다.
        existing_chunks = repo.get_document_chunks(rel_path)
        existing_map = {c["content"]: c["embedding"] for c in existing_chunks if c.get("embedding")}

        # 본문 내 [[WikiLink]] 추출하여 엣지(관계) 저장
        wiki_links = extract_wiki_links(parsed_data["body"])
        source_meta = {"source_path": fm.get("source_path"), "type": doc_type}
        db_edges = []
        
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
            db_edges.append({
                "source_path": edge.source_path,
                "target_topic": edge.target_topic,
                "weight": edge.weight,
            })

        # 청크 수집 (임베딩 전 단계)
        parent_chunks = split_markdown_by_headers(parsed_data["body"])

        pending_chunks_need_embed = []  # 임베딩이 새로 필요한 청크 매핑용 목록
        embedding_texts = []           # 실제 임베딩 요청을 보낼 신규/수정된 텍스트 리스트
        db_chunks = []                 # 최종 DB 업서트 대상 청크 리스트
        
        chunk_index = 0
        expansion_tasks = []           # List of tuples: (task_index, chunk_title, description, child_txt)

        for parent in parent_chunks:
            header = parent["header"]
            parent_txt = parent["content"]
            chunk_title = f"{title} > {header}" if header != "Intro" else title

            child_txts = chunk_text(parent_txt, max_chars=300, overlap=50)

            for child_txt in child_txts:
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

                # 내용(content)이 토씨 하나 안 틀리고 똑같다면 기존 임베딩 재활용!
                if child_txt in existing_map:
                    c_dict = chunk.to_dict()
                    c_dict["embedding"] = existing_map[child_txt]
                    db_chunks.append(c_dict)
                else:
                    # 신규/변경된 청크만 임베딩 대상에 추가
                    emb_text = chunk.to_embedding_text()
                    embedding_texts.append(emb_text)
                    pending_chunks_need_embed.append(chunk)

                    from src.core.config import DOCUMENT_EXPANSION_ENABLED
                    if DOCUMENT_EXPANSION_ENABLED and self.openai_client:
                        # 신규 청크에 대해서만 OpenAI Document Expansion 병렬 태스크 할당
                        task_idx = len(embedding_texts) - 1  # 새로 추가된 인덱스
                        expansion_tasks.append((task_idx, chunk_title, description, child_txt))

                chunk_index += 1

        # 신규/변경된 청크에 대해서만 하이브리드 미니배치 병렬로 OpenAI Document Expansion 호출 실행
        if expansion_tasks:
            # 5개씩 슬라이싱하여 미니배치 생성
            MINI_BATCH_SIZE = 5
            mini_batches = [expansion_tasks[i:i + MINI_BATCH_SIZE] for i in range(0, len(expansion_tasks), MINI_BATCH_SIZE)]
            max_workers = min(len(mini_batches), 5)
            
            print(f"[~] Launching {len(mini_batches)} concurrent LLM mini-batch queries (size {MINI_BATCH_SIZE}) using {max_workers} threads...")
            with ThreadPoolExecutor(max_workers=max_workers) as executor:
                futures = []
                for batch in mini_batches:
                    # Transform elements to (task_idx, child_txt) for payload
                    task_inputs = [(task_idx, child_txt) for task_idx, chunk_title, desc, child_txt in batch]
                    futures.append(executor.submit(self._generate_batch_expansion, title, description, task_inputs))
                
                for future in as_completed(futures):
                    try:
                        batch_results = future.result()
                        for idx, expansion_txt in batch_results:
                            if expansion_txt:
                                # Inject expansion data back into target index
                                embedding_texts[idx] = f"{embedding_texts[idx]}\n\n[Expected Questions & Keywords]\n{expansion_txt}"
                    except Exception as exc:
                        print(f"[-] Document batch expansion failed: {exc}")


        # 신규/변경된 텍스트들에 대해서만 배치 임베딩 요청
        if embedding_texts:
            print(f"[~] Embedding {len(embedding_texts)} new/modified chunks...")
            embeddings = self.embedding_service.embed_batch(embedding_texts)
            
            for chunk, embedding in zip(pending_chunks_need_embed, embeddings):
                c_dict = chunk.to_dict()
                c_dict["embedding"] = embedding
                db_chunks.append(c_dict)

        # 임베딩 준비가 모두 끝난 뒤 청크와 엣지를 하나의 트랜잭션으로 교체합니다.
        # 이 단계 이전에 실패하면 기존 검색 인덱스는 그대로 유지됩니다.
        repo.replace_document(rel_path, db_chunks, db_edges)

        return "created" if is_new else "updated"



    def run_indexing(self, file_paths: Optional[List[str]] = None) -> Dict[str, Any]:
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
        print("Starting LLM-Wiki incremental indexing...")
        
        # 1. DB 초기화 (테이블 및 인덱스 생성)
        self.repository.initialize_db()
        
        # 2. 로컬 파일 목록 및 DB의 파일 해시 맵 획득
        local_files = (
            [path for path in requested_files if self.storage.exists(path)]
            if scoped
            else self._get_local_files()
        )
        
        # 2-1. 로컬 메타데이터 캐시 구축 (가중치 계산용) 및 토픽 DB 맵 생성
        self.topic_metadata = {}
        for f in local_files:
            try:
                content = self.storage.read_text(f)
                parsed_data = parse_markdown_content(content, f)
                fm = parsed_data.get("frontmatter", {})
                title = fm.get("title", "")
                s_path = fm.get("source_path")
                t_type = fm.get("type")
                
                t_slug = posixpath.splitext(posixpath.basename(f))[0].lower()
                metadata = {"source_path": s_path, "type": t_type}
                
                self.topic_metadata[t_slug] = metadata
                if title:
                    self.topic_metadata[title.lower()] = metadata

                # topics/ 하위의 마크다운 파일 구조를 분석하여 DB의 knowledge_topics 테이블 동기화
                if f.replace("\\", "/").startswith("topics/"):
                    parts = f.replace("\\", "/").split("/")
                    # topics/Category/filename.md 형식
                    if len(parts) >= 3:
                        category = parts[1]
                        topic_name = posixpath.splitext(parts[-1])[0].lower()
                        self.repository.upsert_topic(topic_name, category, f)
            except Exception as e:
                print(f"Warning: Failed to sync topic database for {f}: {e}")
                
        db_hashes = (
            self.repository.get_file_hashes(requested_files)
            if scoped
            else self.repository.get_all_file_hashes()
        )
        
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
