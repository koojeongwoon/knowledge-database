import logging


class LoggingRetrievalObserver:
    def __init__(self, logger=None):
        self.logger = logger or logging.getLogger("knowledge_base.retrieval")

    def graph_expansion_failed(self, error: Exception) -> None:
        self.logger.warning("Graph context expansion failed: %s", error)
