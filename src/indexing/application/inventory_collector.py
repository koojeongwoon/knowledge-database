from dataclasses import dataclass
from typing import Any, Callable, Sequence

from src.core.storage.base import BaseStorageManager
from src.indexing.domain.inventory import (
    IndexingInventory,
    InventoryDocument,
    build_indexing_inventory,
)


MarkdownParser = Callable[[str, str], dict[str, Any]]


@dataclass(frozen=True)
class IndexingInventoryCollector:
    storage: BaseStorageManager
    parser: MarkdownParser

    def collect(self, file_paths: Sequence[str]) -> IndexingInventory:
        documents = []
        for file_path in file_paths:
            content = self.storage.read_text(file_path)
            parsed = self.parser(content, file_path)
            frontmatter = parsed.get("frontmatter", {})
            documents.append(
                InventoryDocument(
                    file_path=file_path,
                    content_hash=parsed["content_hash"],
                    title=frontmatter.get("title", ""),
                    source_path=frontmatter.get("source_path"),
                    doc_type=frontmatter.get("type"),
                )
            )
        return build_indexing_inventory(tuple(documents))
