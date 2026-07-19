import logging
import math
from typing import Any, Sequence


logger = logging.getLogger("knowledge_base.retrieval")


class CrossEncoderReranker:
    def __init__(self, model_name: str):
        self.model = None
        try:
            from sentence_transformers import CrossEncoder
            self.model = CrossEncoder(model_name)
            logger.info("Retrieval reranker loaded: %s", model_name)
        except Exception as error:
            logger.warning("Retrieval reranker unavailable: %s", error)

    @property
    def available(self) -> bool:
        return self.model is not None

    def rerank(
        self, query: str, documents: Sequence[dict[str, Any]], limit: int,
    ) -> list[dict[str, Any]]:
        if not documents or not self.model:
            return [dict(document) for document in documents]
        pairs = [
            (query, document.get("parent_content", document["content"]))
            for document in documents
        ]
        scores = self.model.predict(pairs)
        ranked = []
        for document, score in zip(documents, scores):
            candidate = dict(document)
            candidate["reranker_score"] = 1.0 / (1.0 + math.exp(-float(score)))
            ranked.append(candidate)
        return sorted(ranked, key=lambda item: item["reranker_score"], reverse=True)[:limit]
