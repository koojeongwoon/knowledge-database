from typing import Any, Mapping, Protocol


class OntologyShadowOutcomeLike(Protocol):
    enabled: bool
    status: str
    concept_count: int
    relation_count: int


class OntologyShadowPort(Protocol):
    def process_safely(
        self,
        file_path: str,
        frontmatter: Mapping[str, Any] | None,
        source_revision: str | None = None,
    ) -> OntologyShadowOutcomeLike:
        ...

    def delete_safely(self, file_path: str) -> OntologyShadowOutcomeLike:
        ...
