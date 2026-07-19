import datetime
import posixpath
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from src.wiki.domain.synthesis import slugify


@dataclass(frozen=True)
class ResourceSummary:
    file_path: str
    summary_type: str = "DocumentSummary"
    title: str = ""
    description: str = ""
    tags: tuple[str, ...] = ()
    content: str = ""

    @classmethod
    def from_mapping(cls, item: Mapping[str, Any]) -> "ResourceSummary":
        return cls(
            file_path=str(item["file_path"]),
            summary_type=str(item.get("type") or "DocumentSummary"),
            title=str(item.get("title") or ""),
            description=str(item.get("description") or ""),
            tags=tuple(str(tag) for tag in (item.get("tags") or ())),
            content=str(item.get("content") or ""),
        )

    def to_mapping(self) -> dict[str, Any]:
        return {
            "file_path": self.file_path,
            "type": self.summary_type,
            "title": self.title,
            "description": self.description,
            "tags": list(self.tags),
            "content": self.content,
        }


@dataclass(frozen=True)
class KnowledgeCommitCommand:
    title: str
    description: str
    tags: tuple[str, ...]
    content: str
    topic_name: str | None = None
    topic_update_text: str | None = None
    image_paths: tuple[str, ...] = ()
    resource_paths: tuple[str, ...] = ()
    resource_summaries: tuple[ResourceSummary, ...] = ()
    visibility: str = "public"


@dataclass(frozen=True)
class KnowledgeCommitPlan:
    command: KnowledgeCommitCommand
    timestamp: datetime.datetime
    qa_bundle_dir: str
    qa_file_path: str
    resources: tuple[str, ...]
    resource_summaries: tuple[ResourceSummary, ...]


@dataclass(frozen=True)
class KnowledgeCommitResult:
    qa_file_path: str
    topic_file_path: str | None
    all_resources: tuple[str, ...]
    written_paths: tuple[str, ...]
    details: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "qa_file_path": self.qa_file_path,
            "topic_file_path": self.topic_file_path,
            "all_resources": list(self.all_resources),
            "written_paths": list(self.written_paths),
            "details": self.details,
        }


def build_commit_plan(
    command: KnowledgeCommitCommand, timestamp: datetime.datetime,
) -> KnowledgeCommitPlan:
    title_slug = slugify(command.title) or "qa-journal"
    date_part = timestamp.strftime("%Y-%m-%d")
    time_part = timestamp.strftime("%H%M")
    bundle = posixpath.join("qa", date_part, f"{time_part}-{title_slug}")
    summary_paths = tuple(summary.file_path for summary in command.resource_summaries)
    resources = tuple(dict.fromkeys(
        (*command.image_paths, *command.resource_paths, *summary_paths)
    ))
    return KnowledgeCommitPlan(
        command=command,
        timestamp=timestamp,
        qa_bundle_dir=bundle,
        qa_file_path=posixpath.join(bundle, f"{time_part}-{title_slug}.md"),
        resources=resources,
        resource_summaries=command.resource_summaries,
    )


def normalize_written_paths(paths: Sequence[str]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(paths))
