import os
import datetime
from typing import List, Dict, Any

from src.core.storage.factory import StorageManager
from src.core.database.factory import DatabaseManager
from src.indexing.infrastructure.repository import IndexingRepository
from src.wiki.domain.synthesis import (
    slugify,
    build_journal_markdown,
    build_sidecar_markdown,
    synthesize_topic,
    build_new_topic_markdown
)
from src.api.exceptions import StorageOperationException, DatabaseException

class WikiIntegrationManager:
    """
    지식 생명주기 및 통합을 담당하는 명령형 쉘(Imperative Shell).
    디스크 I/O 및 데이터베이스 통신 흐름을 관리하고, 동작은 순수 도메인 함수에 위임합니다.
    """
    def __init__(self):
        self.storage = StorageManager()

    def commit_knowledge(
        self,
        title: str,
        description: str,
        tags: List[str],
        content: str,
        topic_name: str = None,
        topic_update_text: str = None,
        image_paths: List[str] = None,
        resource_paths: List[str] = None,
        resource_summaries: List[Dict[str, Any]] = None,
        visibility: str = "public"
    ) -> Dict[str, Any]:
        now = datetime.datetime.now(datetime.timezone.utc)
        date_str = now.strftime("%Y-%m-%d")
        time_str = now.strftime("%H%M")
        
        # 1. 경로 결정
        title_slug = slugify(title) or "qa-journal"
        qa_bundle_dir = os.path.join("qa", date_str, f"{time_str}-{title_slug}")
        
        try:
            self.storage.makedirs(qa_bundle_dir)
        except Exception as e:
            raise StorageOperationException(f"저장소 디렉토리 생성 실패: {e}") from e

        qa_file_path = os.path.join(qa_bundle_dir, f"{time_str}-{title_slug}.md")

        # 2. 리소스 처리 및 사이드카 생성
        all_resources = []
        if image_paths:
            all_resources.extend(image_paths)
        if resource_paths:
            all_resources.extend(resource_paths)
            
        summary_map = {}
        if resource_summaries:
            for summary in resource_summaries:
                f_path = summary.get("file_path")
                if f_path:
                    all_resources.append(f_path)
                    summary_map[f_path] = summary
                    
        all_resources = list(dict.fromkeys(all_resources))
        resource_info = ""

        if all_resources:
            try:
                assets_dir = os.path.join(qa_bundle_dir, "assets")
                self.storage.makedirs(assets_dir)
                
                copied_images = []
                copied_files = []
                image_extensions = (".png", ".jpg", ".jpeg", ".webp", ".gif")
                
                for res_path in all_resources:
                    if os.path.exists(res_path):
                        filename = os.path.basename(res_path)
                        dest_path = os.path.join(assets_dir, filename)
                        self.storage.copy_file(res_path, dest_path)
                        
                        is_image = filename.lower().endswith(image_extensions)
                        if is_image:
                            copied_images.append(f"![[assets/{filename}]]")
                        else:
                            copied_files.append(f"[[assets/{filename}]]")
                        
                        if res_path in summary_map:
                            summary = summary_map[res_path]
                            sidecar_content = build_sidecar_markdown(filename, summary, now)
                            sidecar_file_path = os.path.join(assets_dir, f"{filename}.md")
                            self.storage.write_text(sidecar_file_path, sidecar_content)
                
                attachments_md = []
                if copied_images:
                    attachments_md.append("### 첨부 이미지\n" + "\n".join(copied_images))
                if copied_files:
                    attachments_md.append("### 첨부 파일 및 리소스\n" + "\n".join(copied_files))
                    
                if attachments_md:
                    content = content + "\n\n" + "\n\n".join(attachments_md)
                    resource_info = f" (자원 {len(copied_images) + len(copied_files)}개 복사 및 사이드카 {len(summary_map)}개 작성)"
            except Exception as e:
                raise StorageOperationException(f"첨부 자원 복사 처리 중 오류 발생: {e}") from e

        # 3. Journal 파일 쓰기
        qa_content = build_journal_markdown(title, description, tags, content, now, visibility)
        try:
            self.storage.write_text(qa_file_path, qa_content)
        except Exception as e:
            raise StorageOperationException(f"Q&A 저널 파일 쓰기 실패: {e}") from e

        # 4. 토픽 합성 (선택 사항)
        topic_info = ""
        topic_file_path = None
        if topic_name:
            topic_slug = slugify(topic_name)
            category = "Development"
            
            try:
                db_manager = DatabaseManager()
                db_manager.connect()
                repo = IndexingRepository(db_manager)
                
                topic_record = repo.get_topic_by_name(topic_slug)
                if topic_record:
                    topic_file_path = topic_record["file_path"]
                    category = topic_record["category"]
                else:
                    topic_files = self.storage.list_files("topics", "*.md")
                    for f_rel in topic_files:
                        if os.path.basename(f_rel) == f"{topic_slug}.md":
                            topic_file_path = f_rel
                            parts = f_rel.replace("\\", "/").split("/")
                            if len(parts) >= 3:
                                category = parts[1]
                            break
                
                if not topic_file_path:
                    topic_file_path = os.path.join("topics", category, f"{topic_slug}.md")
                    
                self.storage.makedirs(os.path.dirname(topic_file_path))
                repo.upsert_topic(topic_slug, category, topic_file_path)
            except Exception as e:
                raise DatabaseException(f"토픽 메타데이터 DB 동기화 실패: {e}") from e
            finally:
                db_manager.close()
                
            if self.storage.exists(topic_file_path):
                try:
                    old_content = self.storage.read_text(topic_file_path)
                    new_content = synthesize_topic(old_content, topic_update_text, now)
                    self.storage.write_text(topic_file_path, new_content)
                    topic_info = f" 및 토픽 '{topic_slug}.md' 누적 합성"
                except Exception as e:
                    raise StorageOperationException(f"기존 토픽 마크다운 업데이트 실패: {e}") from e
            else:
                try:
                    topic_content = build_new_topic_markdown(topic_name, tags, topic_update_text, now)
                    self.storage.write_text(topic_file_path, topic_content)
                    topic_info = f" 및 신규 토픽 '{topic_slug}.md' 생성"
                except Exception as e:
                    raise StorageOperationException(f"신규 토픽 마크다운 생성 실패: {e}") from e

        return {
            "qa_file_path": qa_file_path,
            "topic_file_path": topic_file_path,
            "all_resources": all_resources,
            "details": f"Q&A 저널 작성{topic_info}{resource_info} 완료"
        }
