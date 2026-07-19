import datetime
import posixpath
from collections.abc import Callable
from typing import Any

from src.api.exceptions import DatabaseException, StorageOperationException
from src.wiki.domain.commit import (
    KnowledgeCommitCommand,
    KnowledgeCommitResult,
    ResourceSummary,
    build_commit_plan,
    normalize_written_paths,
)
from src.wiki.domain.ports import IndexingQueue, TopicMetadataRepository
from src.wiki.domain.synthesis import (
    build_journal_markdown,
    build_new_topic_markdown,
    build_sidecar_markdown,
    slugify,
    synthesize_topic,
)


class ResourceCopyExecutor:
    IMAGE_EXTENSIONS = (".png", ".jpg", ".jpeg", ".webp", ".gif")

    def __init__(self, storage):
        self.storage = storage

    def execute(self, plan) -> tuple[str, tuple[str, ...], str]:
        if not plan.resources:
            return plan.command.content, (), ""
        assets_dir = posixpath.join(plan.qa_bundle_dir, "assets")
        copied_images: list[str] = []
        copied_files: list[str] = []
        written: list[str] = []
        summaries = {summary.file_path: summary for summary in plan.resource_summaries}
        try:
            self.storage.makedirs(assets_dir)
            for source_path in plan.resources:
                if not self.storage.exists(source_path):
                    continue
                filename = posixpath.basename(source_path)
                destination = posixpath.join(assets_dir, filename)
                self.storage.copy_file(source_path, destination)
                if filename.lower().endswith(self.IMAGE_EXTENSIONS):
                    copied_images.append(f"![[assets/{filename}]]")
                else:
                    copied_files.append(f"[[assets/{filename}]]")
                summary = summaries.get(source_path)
                if summary is not None:
                    sidecar = posixpath.join(assets_dir, f"{filename}.md")
                    self.storage.write_text(
                        sidecar, build_sidecar_markdown(filename, summary.to_mapping(), plan.timestamp),
                    )
                    written.append(sidecar)
        except Exception as error:
            raise StorageOperationException(f"첨부 자원 복사 처리 중 오류 발생: {error}") from error

        attachments: list[str] = []
        if copied_images:
            attachments.append("### 첨부 이미지\n" + "\n".join(copied_images))
        if copied_files:
            attachments.append("### 첨부 파일 및 리소스\n" + "\n".join(copied_files))
        content = plan.command.content
        if attachments:
            content += "\n\n" + "\n\n".join(attachments)
        count = len(copied_images) + len(copied_files)
        detail = f" (자원 {count}개 복사 및 사이드카 {len(summaries)}개 작성)" if attachments else ""
        return content, tuple(written), detail


class JournalWriteExecutor:
    def __init__(self, storage):
        self.storage = storage

    def execute(self, plan, content: str) -> str:
        try:
            self.storage.write_text(plan.qa_file_path, build_journal_markdown(
                plan.command.title, plan.command.description, list(plan.command.tags),
                content, plan.timestamp, plan.command.visibility,
            ))
            return plan.qa_file_path
        except Exception as error:
            raise StorageOperationException(f"Q&A 저널 파일 쓰기 실패: {error}") from error


class TopicSynthesisExecutor:
    def __init__(self, storage, repository: TopicMetadataRepository):
        self.storage = storage
        self.repository = repository

    def execute(self, plan) -> tuple[str | None, str]:
        topic_name = plan.command.topic_name
        if not topic_name:
            return None, ""
        topic_slug = slugify(topic_name)
        category = "Development"
        try:
            record = self.repository.get_topic_by_name(topic_slug)
            topic_path = record["file_path"] if record else None
            if record:
                category = record["category"]
            if not topic_path:
                for candidate in self.storage.list_files("topics", "*.md"):
                    if posixpath.basename(candidate) == f"{topic_slug}.md":
                        topic_path = candidate
                        parts = candidate.replace("\\", "/").split("/")
                        if len(parts) >= 3:
                            category = parts[1]
                        break
            if not topic_path:
                topic_path = posixpath.join("topics", category, f"{topic_slug}.md")
            self.storage.makedirs(posixpath.dirname(topic_path))
            self.repository.upsert_topic(topic_slug, category, topic_path)
        except Exception as error:
            raise DatabaseException(f"토픽 메타데이터 DB 동기화 실패: {error}") from error

        try:
            if self.storage.exists(topic_path):
                updated = synthesize_topic(
                    self.storage.read_text(topic_path), plan.command.topic_update_text, plan.timestamp,
                )
                self.storage.write_text(topic_path, updated)
                return topic_path, f" 및 토픽 '{topic_slug}.md' 누적 합성"
            created = build_new_topic_markdown(
                topic_name, list(plan.command.tags), plan.command.topic_update_text, plan.timestamp,
            )
            self.storage.write_text(topic_path, created)
            return topic_path, f" 및 신규 토픽 '{topic_slug}.md' 생성"
        except Exception as error:
            action = "기존 토픽 마크다운 업데이트" if self.storage.exists(topic_path) else "신규 토픽 마크다운 생성"
            raise StorageOperationException(f"{action} 실패: {error}") from error


class WikiIntegrationManager:
    def __init__(
        self,
        storage,
        topic_repository: TopicMetadataRepository,
        clock: Callable[[], datetime.datetime],
    ):
        self.storage = storage
        self.topic_repository = topic_repository
        self.clock = clock
        self.resources = ResourceCopyExecutor(storage)
        self.journal = JournalWriteExecutor(storage)
        self.topics = TopicSynthesisExecutor(storage, topic_repository)

    def commit(self, command: KnowledgeCommitCommand) -> KnowledgeCommitResult:
        plan = build_commit_plan(command, self.clock())
        try:
            self.storage.makedirs(plan.qa_bundle_dir)
        except Exception as error:
            raise StorageOperationException(f"저장소 디렉토리 생성 실패: {error}") from error
        content, sidecars, resource_detail = self.resources.execute(plan)
        journal_path = self.journal.execute(plan, content)
        topic_path, topic_detail = self.topics.execute(plan)
        written = normalize_written_paths((*sidecars, journal_path, *([topic_path] if topic_path else [])))
        return KnowledgeCommitResult(
            qa_file_path=journal_path,
            topic_file_path=topic_path,
            all_resources=plan.resources,
            written_paths=written,
            details=f"Q&A 저널 작성{topic_detail}{resource_detail} 완료",
        )

    def commit_knowledge(
        self, title: str, description: str, tags: list[str], content: str,
        topic_name: str = None, topic_update_text: str = None,
        image_paths: list[str] = None, resource_paths: list[str] = None,
        resource_summaries: list[dict[str, Any]] = None,
        visibility: str = "public",
    ) -> dict[str, Any]:
        summaries = tuple(
            ResourceSummary.from_mapping(item)
            for item in (resource_summaries or []) if item.get("file_path")
        )
        return self.commit(KnowledgeCommitCommand(
            title=title, description=description, tags=tuple(tags or ()), content=content,
            topic_name=topic_name, topic_update_text=topic_update_text,
            image_paths=tuple(image_paths or ()), resource_paths=tuple(resource_paths or ()),
            resource_summaries=summaries, visibility=visibility,
        )).to_dict()


class KnowledgeCommitCoordinator:
    def __init__(self, manager: WikiIntegrationManager, queue: IndexingQueue):
        self.manager = manager
        self.queue = queue

    def commit(self, **kwargs) -> dict[str, Any]:
        result = self.manager.commit_knowledge(**kwargs)
        paths = result["written_paths"]
        indexing = {"status": "skipped", "indexed_files": [], "retry_targets": []}
        if paths:
            try:
                self.queue.enqueue(paths)
            except Exception as error:
                raise DatabaseException(
                    "지식 파일은 저장되었지만 인덱싱 작업 등록에 실패했습니다. "
                    f"저장된 파일: {', '.join(paths)}. 원인: {error}"
                ) from error
            indexing = {
                "status": "queued", "indexed_files": [], "retry_targets": [],
                "queued_files": paths,
            }
        return result | {"indexing": indexing}
