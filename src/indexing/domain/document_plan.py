import posixpath
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from src.indexing.domain.model import Edge


@dataclass(frozen=True)
class DocumentMetadata:
    doc_type: str
    title: str
    description: str
    tags: tuple[str, ...]


def resolve_document_metadata(
    file_path: str,
    frontmatter: Mapping[str, Any],
) -> DocumentMetadata:
    doc_type = frontmatter.get("type")
    if not doc_type:
        if file_path.startswith("qa"):
            doc_type = "QAJournal"
        elif file_path.startswith("topics"):
            doc_type = "TopicSummary"
        else:
            doc_type = "Unknown"

    default_title = posixpath.splitext(posixpath.basename(file_path))[0]
    raw_tags = frontmatter.get("tags", ())
    if isinstance(raw_tags, str):
        tags = (raw_tags,)
    else:
        tags = tuple(raw_tags or ())

    return DocumentMetadata(
        doc_type=str(doc_type),
        title=str(frontmatter.get("title") or default_title),
        description=str(frontmatter.get("description") or ""),
        tags=tags,
    )


def plan_document_edges(
    *,
    source_path: str,
    target_topics: Sequence[str],
    source_metadata: Mapping[str, Any],
    topic_metadata: Mapping[str, Mapping[str, Any]],
    custom_relations: Sequence[Mapping[str, Any]] = (),
) -> tuple[Edge, ...]:
    return tuple(
        Edge.create_with_4signal(
            source_path=source_path,
            target_topic=target_topic,
            source_meta=dict(source_metadata),
            target_meta=(
                dict(topic_metadata[target_topic.lower()])
                if target_topic.lower() in topic_metadata
                else None
            ),
            custom_relations=list(custom_relations),
        )
        for target_topic in target_topics
    )
