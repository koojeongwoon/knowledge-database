import logging

from src.indexing.domain.events import IndexingEvent, IndexingEventKind


class LoggingIndexingObserver:
    def __init__(self, logger: logging.Logger | None = None):
        self._logger = logger or logging.getLogger("knowledge.indexing")

    def emit(self, event: IndexingEvent) -> None:
        log = (
            self._logger.warning
            if event.kind in {IndexingEventKind.WARNING, IndexingEventKind.FILE_FAILED}
            else self._logger.info
        )
        log(event.message)
