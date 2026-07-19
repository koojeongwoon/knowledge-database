import datetime

from src.core.database.factory import DatabaseManager
from src.core.storage.factory import StorageManager
from src.indexing.infrastructure.job_repository import IndexingJobRepository
from src.indexing.infrastructure.repository import IndexingRepository
from src.wiki.application.integration import KnowledgeCommitCoordinator, WikiIntegrationManager


class KnowledgeCommitRuntime:
    def __init__(self, coordinator, databases):
        self.coordinator = coordinator
        self.databases = tuple(databases)

    def close(self) -> None:
        for database in self.databases:
            database.close()


def create_knowledge_commit_runtime() -> KnowledgeCommitRuntime:
    topic_database = DatabaseManager()
    queue_database = DatabaseManager()
    manager = WikiIntegrationManager(
        storage=StorageManager(),
        topic_repository=IndexingRepository(topic_database),
        clock=lambda: datetime.datetime.now(datetime.timezone.utc),
    )
    coordinator = KnowledgeCommitCoordinator(
        manager=manager,
        queue=IndexingJobRepository(queue_database),
    )
    return KnowledgeCommitRuntime(coordinator, (topic_database, queue_database))
