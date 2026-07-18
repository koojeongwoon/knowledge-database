from dataclasses import dataclass
from typing import Any, Callable, Mapping, Sequence

from src.indexing.domain.model import Chunk


@dataclass(frozen=True)
class ReusedChunk:
    chunk: Chunk
    embedding: tuple[float, ...]


@dataclass(frozen=True)
class PendingChunk:
    chunk: Chunk
    embedding_text: str


@dataclass(frozen=True)
class DocumentChunkPlan:
    reused: tuple[ReusedChunk, ...] = ()
    pending: tuple[PendingChunk, ...] = ()
    expansion_enabled: bool = False

    @property
    def embedding_texts(self) -> tuple[str, ...]:
        return tuple(item.embedding_text for item in self.pending)

    @property
    def expansion_tasks(self) -> tuple[tuple[int, str], ...]:
        if not self.expansion_enabled:
            return ()
        return tuple(
            (index, item.chunk.content)
            for index, item in enumerate(self.pending)
        )


def plan_document_chunks(
    *,
    file_path: str,
    doc_type: str,
    title: str,
    description: str,
    tags: Sequence[str],
    raw_frontmatter: Mapping[str, Any],
    content_hash: str,
    parents: Sequence[Mapping[str, str]],
    existing_embeddings: Mapping[str, Sequence[float]],
    expansion_enabled: bool,
    chunker: Callable[[str], Sequence[str]],
) -> DocumentChunkPlan:
    """Build a deterministic chunk plan without storage, database, or API I/O."""

    reused: list[ReusedChunk] = []
    pending: list[PendingChunk] = []
    chunk_index = 0

    for parent in parents:
        header = parent["header"]
        parent_content = parent["content"]
        chunk_title = f"{title} > {header}" if header != "Intro" else title
        for content in chunker(parent_content):
            chunk = Chunk(
                file_path=file_path,
                chunk_index=chunk_index,
                doc_type=doc_type,
                title=chunk_title,
                description=description,
                tags=list(tags),
                content=content,
                parent_content=parent_content,
                raw_frontmatter=dict(raw_frontmatter),
                content_hash=content_hash,
            )
            embedding = existing_embeddings.get(content)
            if embedding is None:
                pending.append(PendingChunk(chunk, chunk.to_embedding_text()))
            else:
                reused.append(ReusedChunk(chunk, tuple(embedding)))
            chunk_index += 1

    return DocumentChunkPlan(
        reused=tuple(reused),
        pending=tuple(pending),
        expansion_enabled=expansion_enabled,
    )


def materialize_chunk_records(
    plan: DocumentChunkPlan,
    embeddings: Sequence[Sequence[float]],
) -> tuple[dict[str, Any], ...]:
    """Attach embeddings only when every pending chunk has exactly one result."""

    if len(embeddings) != len(plan.pending):
        raise ValueError(
            "Pending chunk count and embedding count must match: "
            f"{len(plan.pending)} != {len(embeddings)}"
        )

    records: list[dict[str, Any]] = []
    for reused in plan.reused:
        record = reused.chunk.to_dict()
        record["embedding"] = list(reused.embedding)
        records.append(record)
    for pending, embedding in zip(plan.pending, embeddings):
        record = pending.chunk.to_dict()
        record["embedding"] = list(embedding)
        records.append(record)
    return tuple(records)
