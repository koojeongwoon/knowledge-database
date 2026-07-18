import re
import unicodedata
from typing import Any, Mapping

from src.ontology.domain.model import Concept, DocumentConcept, OntologySnapshot, Relation


def normalize_concept_id(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", str(value)).strip().lower()
    normalized = re.sub(r"[^0-9a-z가-힣]+", "-", normalized, flags=re.UNICODE)
    return normalized.strip("-")


def normalize_alias(value: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", str(value)).casefold().split())


class ExplicitOntologyExtractor:
    """Parse only human-authored `ontology` frontmatter; never infer facts from prose."""

    def extract(
        self,
        file_path: str,
        frontmatter: Mapping[str, Any] | None,
        *,
        source_revision: str | None = None,
    ) -> OntologySnapshot:
        payload = (frontmatter or {}).get("ontology")
        if payload is None:
            return OntologySnapshot(source_path=file_path)
        if not isinstance(payload, Mapping):
            raise ValueError("Frontmatter ontology must be a mapping.")

        concepts = tuple(self._concept(item) for item in payload.get("concepts", []))
        relations = tuple(
            self._relation(item, source_revision=source_revision)
            for item in payload.get("relations", [])
        )
        declared_links = payload.get("document_concepts")
        if declared_links is None:
            links = tuple(
                DocumentConcept(file_path=file_path, concept_id=concept.concept_id)
                for concept in concepts
            )
        else:
            links = tuple(self._document_concept(file_path, item) for item in declared_links)
        return OntologySnapshot(file_path, concepts, relations, links)

    def _concept(self, item: Any) -> Concept:
        if not isinstance(item, Mapping):
            raise ValueError("Ontology concept entries must be mappings.")
        canonical_name = str(item.get("name", "")).strip()
        concept_id = normalize_concept_id(item.get("id") or canonical_name)
        aliases = tuple(
            alias for alias in dict.fromkeys(
                normalize_alias(value) for value in item.get("aliases", [])
            ) if alias
        )
        return Concept(
            concept_id=concept_id,
            canonical_name=canonical_name,
            kind=str(item.get("kind", "concept")).strip() or "concept",
            description=str(item.get("description", "")).strip(),
            aliases=aliases,
            status=str(item.get("status", "approved")),
            metadata=dict(item.get("metadata", {})),
        )

    def _relation(self, item: Any, *, source_revision: str | None = None) -> Relation:
        if not isinstance(item, Mapping):
            raise ValueError("Ontology relation entries must be mappings.")
        status = str(item.get("status", "asserted")).strip().lower()
        status = {"approved": "asserted", "draft": "pending", "deprecated": "revoked"}.get(status, status)
        return Relation(
            subject=normalize_concept_id(item.get("subject", "")),
            predicate=str(item.get("predicate", "")).strip().lower(),
            object=normalize_concept_id(item.get("object", "")),
            status=status,
            confidence=float(item.get("confidence", 1.0)),
            scope=dict(item.get("scope", {})),
            valid_from=item.get("valid_from"),
            valid_to=item.get("valid_to"),
            evidence_text=str(item.get("evidence", "")).strip(),
            evidence_location=dict(item.get("evidence_location", {})),
            evidence_hash=item.get("evidence_hash"),
            source_revision=source_revision,
            extractor_type="human",
            ontology_schema_version=str(item.get("ontology_schema_version", "ontology-v1")),
            metadata=dict(item.get("metadata", {})),
        )

    def _document_concept(self, file_path: str, item: Any) -> DocumentConcept:
        if isinstance(item, str):
            return DocumentConcept(file_path, normalize_concept_id(item))
        if not isinstance(item, Mapping):
            raise ValueError("Document concept entries must be strings or mappings.")
        return DocumentConcept(
            file_path=file_path,
            concept_id=normalize_concept_id(item.get("concept", "")),
            confidence=float(item.get("confidence", 1.0)),
        )
