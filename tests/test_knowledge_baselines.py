import hashlib

import pytest

from src.baselines.service import BaselineService, is_baseline_path, normalize_live_source_path


class MemoryStorage:
    def __init__(self, files):
        self.files = dict(files)
        self.fail_delete = False

    def read_text(self, path):
        if path not in self.files:
            raise FileNotFoundError(path)
        return self.files[path]

    def write_text(self, path, content):
        self.files[path] = content

    def copy_file(self, source, destination):
        self.files[destination] = self.files[source]

    def exists(self, path):
        return path in self.files

    def delete_file(self, path):
        if self.fail_delete:
            raise OSError("cleanup failed")
        prefix = path.rstrip("/") + "/"
        self.files = {key: value for key, value in self.files.items()
                      if key != path and not key.startswith(prefix)}


class MemoryRepository:
    def __init__(self):
        self.drafts = {}
        self.confirmed = []
        self.indexed = True
        self.fail_after_materialize = False

    def create_draft(self, owner_id, draft):
        self.drafts[(owner_id, draft.draft_id)] = draft

    def get_pending_draft(self, owner_id, draft_id):
        return self.drafts.get((owner_id, draft_id))

    def confirm(self, owner_id, draft, release_id, release_dir, manifest_hash, materialize):
        if not self.indexed:
            raise ValueError("최신 원본과 일치하는 인덱스가 없습니다.")
        materialize()
        if self.fail_after_materialize:
            raise RuntimeError("database commit failed")
        self.confirmed.append((owner_id, draft, release_id, release_dir, manifest_hash))
        self.drafts.pop((owner_id, draft.draft_id))


def _service():
    storage = MemoryStorage({
        "qa/auth.md": "# Auth\nApproved policy",
        "topics/oauth.md": "# OAuth\nPKCE",
    })
    repository = MemoryRepository()
    return BaselineService("USER_1", storage, repository), storage, repository


def test_prepare_only_creates_a_pending_manifest():
    service, storage, repository = _service()

    result = service.prepare(
        name="OAuth 설계 기준", version="v1", purpose="설계 검토",
        source_paths=["qa/auth.md", "topics/oauth.md"],
    )

    assert result["status"] == "pending"
    assert result["directory_path"].startswith("baseline-drafts/")
    assert f"{result['directory_path']}/baseline.yaml" in storage.files
    assert not any(path.startswith("baselines/") for path in storage.files)
    assert ("USER_1", result["draft_id"]) in repository.drafts


def test_confirm_copies_immutable_snapshots_to_a_separate_version_directory():
    service, storage, repository = _service()
    draft = service.prepare(
        name="oauth", version="v1", purpose="review", source_paths=["qa/auth.md"],
    )

    result = service.confirm(draft["draft_id"])

    assert result["status"] == "confirmed"
    assert result["directory_path"] == "baselines/oauth/v1"
    assert storage.files["baselines/oauth/v1/documents/qa/auth.md"] == storage.files["qa/auth.md"]
    assert len(repository.confirmed) == 1


def test_confirm_rejects_a_source_changed_after_review():
    service, storage, _repository = _service()
    draft = service.prepare(
        name="oauth", version="v1", purpose="review", source_paths=["qa/auth.md"],
    )
    storage.files["qa/auth.md"] = "changed after review"

    with pytest.raises(ValueError, match="원본이 변경"):
        service.confirm(draft["draft_id"])


def test_confirm_rejects_a_stale_index_before_writing_release_files():
    service, storage, repository = _service()
    draft = service.prepare(
        name="oauth", version="v1", purpose="review", source_paths=["qa/auth.md"],
    )
    repository.indexed = False

    with pytest.raises(ValueError, match="인덱스"):
        service.confirm(draft["draft_id"])

    assert not any(path.startswith("baselines/") for path in storage.files)
    assert ("USER_1", draft["draft_id"]) in repository.drafts


def test_confirm_removes_only_its_materialized_release_when_database_finalize_fails():
    service, storage, repository = _service()
    draft = service.prepare(
        name="oauth", version="v1", purpose="review", source_paths=["qa/auth.md"],
    )
    repository.fail_after_materialize = True

    with pytest.raises(RuntimeError, match="database commit failed"):
        service.confirm(draft["draft_id"])

    assert not any(path.startswith("baselines/oauth/v1/") for path in storage.files)
    assert ("USER_1", draft["draft_id"]) in repository.drafts


def test_existing_confirmed_directory_is_not_deleted_by_losing_confirmation():
    service, storage, _repository = _service()
    draft = service.prepare(
        name="oauth", version="v1", purpose="review", source_paths=["qa/auth.md"],
    )
    storage.files["baselines/oauth/v1/baseline.yaml"] = "winner"

    with pytest.raises(ValueError, match="이미 존재"):
        service.confirm(draft["draft_id"])

    assert storage.files["baselines/oauth/v1/baseline.yaml"] == "winner"


def test_cleanup_failure_does_not_hide_database_failure():
    service, storage, repository = _service()
    draft = service.prepare(
        name="oauth", version="v1", purpose="review", source_paths=["qa/auth.md"],
    )
    repository.fail_after_materialize = True
    storage.fail_delete = True

    with pytest.raises(RuntimeError, match="database commit failed") as captured:
        service.confirm(draft["draft_id"])

    assert any("스토리지 정리도 실패" in note for note in captured.value.__notes__)


def test_wrapped_domain_conflict_is_restored_for_the_api_boundary():
    service, _storage, repository = _service()
    draft = service.prepare(
        name="oauth", version="v1", purpose="review", source_paths=["qa/auth.md"],
    )

    def wrapped_conflict(*_args):
        try:
            raise ValueError("이미 처리된 기준본입니다.")
        except ValueError as domain_error:
            raise RuntimeError("database transaction failed") from domain_error

    repository.confirm = wrapped_conflict

    with pytest.raises(ValueError, match="이미 처리된 기준본"):
        service.confirm(draft["draft_id"])


def test_only_live_knowledge_can_be_selected_as_a_baseline_source():
    assert normalize_live_source_path("qa/a.md") == "qa/a.md"
    assert normalize_live_source_path("topics/a.md") == "topics/a.md"
    for path in ("baselines/a/v1/a.md", "baseline-drafts/id/a.md", "../qa/a.md", "assets/a.md"):
        with pytest.raises(ValueError):
            normalize_live_source_path(path)


def test_baseline_directories_are_recognized_for_search_and_indexing_exclusion():
    assert is_baseline_path("baselines/oauth/v1/baseline.yaml")
    assert is_baseline_path("baseline-drafts/id/baseline.yaml")
    assert not is_baseline_path("qa/auth.md")


def test_manifest_hash_is_sha256():
    service, _storage, _repository = _service()
    draft = service.prepare(
        name="oauth", version="v1", purpose="review", source_paths=["qa/auth.md"],
    )
    result = service.confirm(draft["draft_id"])
    assert len(result["manifest_sha256"]) == hashlib.sha256().digest_size * 2
