from dataclasses import dataclass
from typing import Mapping


@dataclass(frozen=True)
class IndexingPlan:
    """Immutable decision about how indexed files differ from stored files."""

    created: tuple[str, ...] = ()
    updated: tuple[str, ...] = ()
    deleted: tuple[str, ...] = ()
    skipped: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        categories = (self.created, self.updated, self.deleted, self.skipped)
        all_paths = tuple(path for category in categories for path in category)
        if len(all_paths) != len(set(all_paths)):
            raise ValueError("Indexing plan categories must be disjoint.")

    @property
    def processing_targets(self) -> tuple[tuple[str, bool], ...]:
        return tuple((path, True) for path in self.created) + tuple(
            (path, False) for path in self.updated
        )


def plan_indexing_changes(
    local_hashes: Mapping[str, str],
    indexed_hashes: Mapping[str, str],
) -> IndexingPlan:
    """Return a deterministic plan without reading storage or mutating state."""

    local_paths = set(local_hashes)
    indexed_paths = set(indexed_hashes)
    shared_paths = local_paths & indexed_paths

    return IndexingPlan(
        created=tuple(sorted(local_paths - indexed_paths)),
        updated=tuple(
            sorted(
                path
                for path in shared_paths
                if local_hashes[path] != indexed_hashes[path]
            )
        ),
        deleted=tuple(sorted(indexed_paths - local_paths)),
        skipped=tuple(
            sorted(
                path
                for path in shared_paths
                if local_hashes[path] == indexed_hashes[path]
            )
        ),
    )
