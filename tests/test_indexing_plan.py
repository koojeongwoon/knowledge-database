import pytest

from src.indexing.domain.plan import IndexingPlan, plan_indexing_changes


def test_plans_created_updated_deleted_and_skipped_files() -> None:
    plan = plan_indexing_changes(
        local_hashes={
            "qa/new.md": "new-hash",
            "qa/changed.md": "new-hash",
            "topics/same.md": "same-hash",
        },
        indexed_hashes={
            "qa/changed.md": "old-hash",
            "topics/same.md": "same-hash",
            "topics/removed.md": "old-hash",
        },
    )

    assert plan == IndexingPlan(
        created=("qa/new.md",),
        updated=("qa/changed.md",),
        deleted=("topics/removed.md",),
        skipped=("topics/same.md",),
    )
    assert plan.processing_targets == (
        ("qa/new.md", True),
        ("qa/changed.md", False),
    )


def test_plan_is_deterministic_regardless_of_mapping_order() -> None:
    plan = plan_indexing_changes(
        local_hashes={"topics/z.md": "1", "qa/a.md": "1"},
        indexed_hashes={},
    )

    assert plan.created == ("qa/a.md", "topics/z.md")


def test_rejects_overlapping_plan_categories() -> None:
    with pytest.raises(ValueError, match="disjoint"):
        IndexingPlan(created=("qa/a.md",), updated=("qa/a.md",))
