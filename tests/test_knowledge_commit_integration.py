import datetime

import pytest

from src.api.exceptions import DatabaseException
from src.wiki.application.integration import KnowledgeCommitCoordinator, WikiIntegrationManager
from src.wiki.domain.commit import KnowledgeCommitCommand, ResourceSummary, build_commit_plan


NOW = datetime.datetime(2026, 7, 19, 1, 23, tzinfo=datetime.timezone.utc)


class MemoryStorage:
    def __init__(self, files=None):
        self.files = dict(files or {})

    def makedirs(self, _path):
        return None

    def exists(self, path):
        return path in self.files

    def read_text(self, path):
        return self.files[path]

    def write_text(self, path, content):
        self.files[path] = content

    def copy_file(self, source, destination):
        self.files[destination] = self.files[source]

    def list_files(self, target, _pattern):
        prefix = target.rstrip("/") + "/"
        return sorted(path for path in self.files if path.startswith(prefix) and path.endswith(".md"))


class TopicRepository:
    def __init__(self, records=None):
        self.records = dict(records or {})
        self.upserts = []

    def get_topic_by_name(self, name):
        return self.records.get(name)

    def upsert_topic(self, name, category, path):
        self.upserts.append((name, category, path))


class Queue:
    def __init__(self, error=None):
        self.error = error
        self.calls = []

    def enqueue(self, paths):
        self.calls.append(tuple(paths))
        if self.error:
            raise self.error


def _manager(storage=None, topics=None):
    return WikiIntegrationManager(
        storage or MemoryStorage(), topics or TopicRepository(), clock=lambda: NOW,
    )


def test_commit_plan_paths_and_resources_are_deterministic():
    command = KnowledgeCommitCommand(
        title="OAuth 기준", description="", tags=("oauth",), content="body",
        image_paths=("tmp/a.png",), resource_paths=("tmp/a.png", "tmp/b.pdf"),
        resource_summaries=(ResourceSummary("tmp/b.pdf", title="B"),),
    )

    first = build_commit_plan(command, NOW)
    second = build_commit_plan(command, NOW)

    assert first == second
    assert first.qa_file_path == "qa/2026-07-19/0123-oauth-기준/0123-oauth-기준.md"
    assert first.resources == ("tmp/a.png", "tmp/b.pdf")


def test_commit_writes_sidecar_journal_and_topic_in_deterministic_order():
    storage = MemoryStorage({
        "tmp/report.pdf": "binary-placeholder",
        "topics/Development/oauth.md": "---\ntimestamp: old\n---\nold",
    })
    topics = TopicRepository({
        "oauth": {"file_path": "topics/Development/oauth.md", "category": "Development"},
    })
    manager = _manager(storage, topics)

    result = manager.commit_knowledge(
        title="OAuth", description="desc", tags=["oauth"], content="new knowledge",
        topic_name="OAuth", topic_update_text="updated",
        resource_summaries=[{"file_path": "tmp/report.pdf", "title": "Report"}],
        visibility="private",
    )

    assert result["written_paths"] == [
        "qa/2026-07-19/0123-oauth/assets/report.pdf.md",
        "qa/2026-07-19/0123-oauth/0123-oauth.md",
        "topics/Development/oauth.md",
    ]
    assert 'visibility: "private"' in storage.files[result["qa_file_path"]]
    assert "[[assets/report.pdf]]" in storage.files[result["qa_file_path"]]
    assert "### 업데이트 (2026-07-19)" in storage.files["topics/Development/oauth.md"]


def test_coordinator_enqueues_only_after_files_are_saved():
    storage = MemoryStorage()
    queue = Queue()
    coordinator = KnowledgeCommitCoordinator(_manager(storage), queue)

    result = coordinator.commit(
        title="A", description="", tags=[], content="body", visibility="private",
    )

    assert result["qa_file_path"] in storage.files
    assert queue.calls == [(result["qa_file_path"],)]
    assert result["indexing"]["status"] == "queued"


def test_queue_failure_reports_saved_paths_for_retry():
    storage = MemoryStorage()
    coordinator = KnowledgeCommitCoordinator(
        _manager(storage), Queue(RuntimeError("queue unavailable")),
    )

    with pytest.raises(DatabaseException, match="파일은 저장되었지만") as error:
        coordinator.commit(title="A", description="", tags=[], content="body")

    assert "qa/2026-07-19/0123-a/0123-a.md" in str(error.value)
    assert "qa/2026-07-19/0123-a/0123-a.md" in storage.files
