from dataclasses import dataclass
import time
from typing import Any, Mapping

from src.ontology.extractor import ExplicitOntologyExtractor


@dataclass(frozen=True)
class OntologyShadowOutcome:
    enabled: bool
    persisted: bool
    status: str = "disabled"
    concept_count: int = 0
    relation_count: int = 0
    document_concept_count: int = 0
    duration_ms: float = 0.0
    error_type: str | None = None
    error_message: str | None = None


class OntologyShadowService:
    """Feature-gated ontology side path with no dependency on direct retrieval."""

    def __init__(
        self,
        repository: Any,
        *,
        shadow_enabled: bool | None = None,
        indexing_enabled: bool | None = None,
        extractor: ExplicitOntologyExtractor | None = None,
    ):
        if shadow_enabled is None or indexing_enabled is None:
            from src.core.config import ONTOLOGY_INDEXING_ENABLED, ONTOLOGY_SHADOW_ENABLED

            shadow_enabled = ONTOLOGY_SHADOW_ENABLED if shadow_enabled is None else shadow_enabled
            indexing_enabled = ONTOLOGY_INDEXING_ENABLED if indexing_enabled is None else indexing_enabled
        self.repository = repository
        self.shadow_enabled = bool(shadow_enabled)
        self.indexing_enabled = bool(indexing_enabled)
        self.extractor = extractor or ExplicitOntologyExtractor()

    def process(
        self,
        file_path: str,
        frontmatter: Mapping[str, Any] | None,
        source_revision: str | None = None,
    ) -> OntologyShadowOutcome:
        if not self.shadow_enabled:
            return OntologyShadowOutcome(enabled=False, persisted=False)

        snapshot = self.extractor.extract(
            file_path, frontmatter, source_revision=source_revision,
        )
        persisted = False
        if self.indexing_enabled:
            self.repository.replace_explicit_snapshot(snapshot)
            persisted = True
        return OntologyShadowOutcome(
            enabled=True,
            persisted=persisted,
            status="persisted" if persisted else "observed",
            concept_count=len(snapshot.concepts),
            relation_count=len(snapshot.relations),
            document_concept_count=len(snapshot.document_concepts),
        )

    def process_safely(
        self, file_path: str, frontmatter: Mapping[str, Any] | None,
        source_revision: str | None = None,
    ) -> OntologyShadowOutcome:
        """Best-effort boundary: ontology failures never escape into direct indexing."""
        started = time.perf_counter()
        try:
            outcome = self.process(file_path, frontmatter, source_revision)
        except Exception as exc:
            outcome = OntologyShadowOutcome(
                enabled=True,
                persisted=False,
                status="error",
                error_type=type(exc).__name__,
                error_message=str(exc),
            )
        outcome = OntologyShadowOutcome(
            **{
                **outcome.__dict__,
                "duration_ms": round((time.perf_counter() - started) * 1000, 3),
            }
        )
        if outcome.enabled:
            try:
                self.repository.record_shadow_event(file_path, outcome)
            except Exception:
                # Telemetry must be best-effort and cannot change indexing success.
                pass
        return outcome

    def delete_safely(self, file_path: str) -> OntologyShadowOutcome:
        """Remove only explicit ontology provenance belonging to a deleted file."""
        started = time.perf_counter()
        if not self.shadow_enabled:
            return OntologyShadowOutcome(enabled=False, persisted=False)
        try:
            if self.indexing_enabled:
                self.repository.delete_explicit_source(file_path)
            outcome = OntologyShadowOutcome(
                enabled=True,
                persisted=self.indexing_enabled,
                status="persisted" if self.indexing_enabled else "observed",
            )
        except Exception as exc:
            outcome = OntologyShadowOutcome(
                enabled=True,
                persisted=False,
                status="error",
                error_type=type(exc).__name__,
                error_message=str(exc),
            )
        outcome = OntologyShadowOutcome(
            **{
                **outcome.__dict__,
                "duration_ms": round((time.perf_counter() - started) * 1000, 3),
            }
        )
        try:
            self.repository.record_shadow_event(file_path, outcome)
        except Exception:
            pass
        return outcome
