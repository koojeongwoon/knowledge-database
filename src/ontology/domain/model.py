from dataclasses import dataclass, field
from typing import Any, Mapping


ALLOWED_PREDICATES = frozenset({
    "uses",
    "depends_on",
    "is_a",
    "part_of",
    "supersedes",
    "contradicts",
    "prohibits",
    "requires",
    "related_to",
})


@dataclass(frozen=True)
class Concept:
    concept_id: str
    canonical_name: str
    kind: str = "concept"
    description: str = ""
    aliases: tuple[str, ...] = ()
    status: str = "approved"
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.concept_id or not self.canonical_name:
            raise ValueError("Ontology concepts require concept_id and canonical_name.")
        if self.status not in {"draft", "approved", "deprecated"}:
            raise ValueError(f"Unsupported concept status: {self.status}")


@dataclass(frozen=True)
class Relation:
    subject: str
    predicate: str
    object: str
    status: str = "asserted"
    confidence: float = 1.0
    scope: Mapping[str, Any] = field(default_factory=dict)
    valid_from: str | None = None
    valid_to: str | None = None
    evidence_text: str = ""
    evidence_location: Mapping[str, Any] = field(default_factory=dict)
    evidence_hash: str | None = None
    source_revision: str | None = None
    extractor_type: str = "human"
    model_name: str | None = None
    model_version: str | None = None
    prompt_version: str | None = None
    ontology_schema_version: str = "ontology-v1"
    reviewed_by: str | None = None
    reviewed_at: str | None = None
    review_reason: str | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.predicate not in ALLOWED_PREDICATES:
            raise ValueError(f"Unsupported ontology predicate: {self.predicate}")
        if not self.subject or not self.object:
            raise ValueError("Ontology relations require subject and object concept IDs.")
        if self.status not in {"inferred", "pending", "asserted", "rejected", "stale", "revoked"}:
            raise ValueError(f"Unsupported relation status: {self.status}")
        if not 0 <= self.confidence <= 1:
            raise ValueError("Ontology relation confidence must be between 0 and 1.")
        if self.extractor_type not in {"human", "llm", "rule"}:
            raise ValueError(f"Unsupported ontology extractor type: {self.extractor_type}")
        if self.valid_from and self.valid_to and self.valid_from > self.valid_to:
            raise ValueError("Ontology relation valid_from cannot be later than valid_to.")


@dataclass(frozen=True)
class DocumentConcept:
    file_path: str
    concept_id: str
    confidence: float = 1.0

    def __post_init__(self) -> None:
        if not self.file_path or not self.concept_id:
            raise ValueError("Document concept links require file_path and concept_id.")
        if not 0 <= self.confidence <= 1:
            raise ValueError("Document concept confidence must be between 0 and 1.")


@dataclass(frozen=True)
class OntologySnapshot:
    source_path: str
    concepts: tuple[Concept, ...] = ()
    relations: tuple[Relation, ...] = ()
    document_concepts: tuple[DocumentConcept, ...] = ()

    def __post_init__(self) -> None:
        if not self.source_path:
            raise ValueError("Ontology snapshots require a source_path.")
        concept_ids = {concept.concept_id for concept in self.concepts}
        if len(concept_ids) != len(self.concepts):
            raise ValueError("Ontology concept IDs must be unique within a document.")
        unknown = {
            ref
            for relation in self.relations
            for ref in (relation.subject, relation.object)
            if ref not in concept_ids
        }
        unknown.update(
            link.concept_id for link in self.document_concepts
            if link.concept_id not in concept_ids
        )
        if unknown:
            raise ValueError(f"Ontology snapshot references undeclared concepts: {sorted(unknown)}")
