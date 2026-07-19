from src.core.database.factory import DatabaseManager
from src.learning.application.session_service import LearningSessionService
from src.learning.infrastructure.identity import create_uuid
from src.learning.infrastructure.repository import LearningSessionRepository


def create_learning_session_service() -> LearningSessionService:
    return LearningSessionService(
        repository=LearningSessionRepository(DatabaseManager()),
        uuid_factory=create_uuid,
    )
