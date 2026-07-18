import posixpath
from dataclasses import dataclass


@dataclass(frozen=True)
class InventoryDocument:
    file_path: str
    content_hash: str
    title: str = ""
    source_path: str | None = None
    doc_type: str | None = None


@dataclass(frozen=True)
class TopicMetadata:
    source_path: str | None = None
    doc_type: str | None = None


@dataclass(frozen=True)
class TopicSyncCommand:
    topic_name: str
    category: str
    file_path: str


@dataclass(frozen=True)
class IndexingInventory:
    hash_entries: tuple[tuple[str, str], ...] = ()
    metadata_entries: tuple[tuple[str, TopicMetadata], ...] = ()
    topic_sync_commands: tuple[TopicSyncCommand, ...] = ()

    @property
    def local_hashes(self) -> dict[str, str]:
        return dict(self.hash_entries)

    @property
    def topic_metadata(self) -> dict[str, TopicMetadata]:
        return dict(self.metadata_entries)

    @property
    def edge_metadata(self) -> dict[str, dict[str, str | None]]:
        return {
            alias: {
                "source_path": metadata.source_path,
                "type": metadata.doc_type,
            }
            for alias, metadata in self.metadata_entries
        }


def build_indexing_inventory(
    documents: tuple[InventoryDocument, ...],
) -> IndexingInventory:
    hashes: dict[str, str] = {}
    metadata: dict[str, TopicMetadata] = {}
    commands: list[TopicSyncCommand] = []

    for document in sorted(documents, key=lambda item: item.file_path):
        hashes[document.file_path] = document.content_hash
        topic_metadata = TopicMetadata(document.source_path, document.doc_type)
        slug = posixpath.splitext(posixpath.basename(document.file_path))[0].lower()
        metadata[slug] = topic_metadata
        if document.title:
            metadata[document.title.lower()] = topic_metadata

        normalized_path = document.file_path.replace("\\", "/")
        parts = normalized_path.split("/")
        if parts[0] == "topics" and len(parts) >= 3:
            commands.append(
                TopicSyncCommand(
                    topic_name=posixpath.splitext(parts[-1])[0].lower(),
                    category=parts[1],
                    file_path=document.file_path,
                )
            )

    return IndexingInventory(
        hash_entries=tuple(sorted(hashes.items())),
        metadata_entries=tuple(sorted(metadata.items())),
        topic_sync_commands=tuple(sorted(commands, key=lambda item: item.file_path)),
    )
