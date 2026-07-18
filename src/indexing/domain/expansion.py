from abc import ABC, abstractmethod
from typing import Sequence


ExpansionTask = tuple[int, str]
ExpansionResult = tuple[int, str]


class BaseDocumentExpander(ABC):
    @property
    @abstractmethod
    def enabled(self) -> bool:
        pass

    @abstractmethod
    def expand_batch(
        self,
        title: str,
        description: str,
        tasks: Sequence[ExpansionTask],
    ) -> tuple[ExpansionResult, ...]:
        pass


class NoOpDocumentExpander(BaseDocumentExpander):
    @property
    def enabled(self) -> bool:
        return False

    def expand_batch(
        self,
        title: str,
        description: str,
        tasks: Sequence[ExpansionTask],
    ) -> tuple[ExpansionResult, ...]:
        return ()
