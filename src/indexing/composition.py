from src.core.database.base import BaseDatabaseManager
from src.core.database.factory import DatabaseManager
from src.core.storage.factory import StorageManager
from src.indexing.application.expansion_executor import DocumentExpansionExecutor
from src.indexing.application.file_executor import FileIndexingExecutor
from src.indexing.application.inventory_collector import IndexingInventoryCollector
from src.indexing.application.service import WikiIndexer
from src.indexing.domain.embedding import BaseEmbeddingService
from src.indexing.domain.events import IndexingObserver
from src.indexing.infrastructure.expansion import create_document_expander
from src.indexing.infrastructure.observer import LoggingIndexingObserver
from src.indexing.infrastructure.repository import IndexingRepository
from src.ontology.composition import create_ontology_shadow
from src.wiki.domain.parser import parse_markdown_content


def create_wiki_indexer(
    db_manager: BaseDatabaseManager,
    embedding_service: BaseEmbeddingService,
    *,
    observer: IndexingObserver | None = None,
) -> WikiIndexer:
    storage = StorageManager()
    return WikiIndexer(
        db_manager=db_manager,
        embedding_service=embedding_service,
        storage=storage,
        repository_factory=IndexingRepository,
        file_executor=FileIndexingExecutor(DatabaseManager),
        expansion_executor=DocumentExpansionExecutor(create_document_expander()),
        inventory_collector=IndexingInventoryCollector(storage, parse_markdown_content),
        ontology_shadow_factory=create_ontology_shadow,
        observer=observer or LoggingIndexingObserver(),
    )
