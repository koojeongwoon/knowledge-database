from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from typing import Sequence

from src.indexing.domain.expansion import BaseDocumentExpander, ExpansionTask


@dataclass(frozen=True)
class DocumentExpansionExecutor:
    expander: BaseDocumentExpander
    batch_size: int = 5
    max_workers: int = 5

    def __post_init__(self) -> None:
        if self.batch_size < 1 or self.max_workers < 1:
            raise ValueError("Document expansion concurrency values must be positive.")

    @property
    def enabled(self) -> bool:
        return self.expander.enabled

    def expand(
        self,
        *,
        title: str,
        description: str,
        embedding_texts: Sequence[str],
        tasks: Sequence[ExpansionTask],
    ) -> tuple[str, ...]:
        expanded_texts = list(embedding_texts)
        if not tasks or not self.enabled:
            return tuple(expanded_texts)

        batches = tuple(
            tuple(tasks[index:index + self.batch_size])
            for index in range(0, len(tasks), self.batch_size)
        )
        worker_count = min(len(batches), self.max_workers)
        with ThreadPoolExecutor(max_workers=worker_count) as executor:
            futures = (
                executor.submit(
                    self.expander.expand_batch,
                    title,
                    description,
                    batch,
                )
                for batch in batches
            )
            for future in as_completed(futures):
                try:
                    for index, expansion_text in future.result():
                        if expansion_text and 0 <= index < len(expanded_texts):
                            expanded_texts[index] = (
                                f"{expanded_texts[index]}\n\n"
                                "[Expected Questions & Keywords]\n"
                                f"{expansion_text}"
                            )
                except Exception as exc:
                    print(f"[-] Document batch expansion failed: {exc}")
        return tuple(expanded_texts)
