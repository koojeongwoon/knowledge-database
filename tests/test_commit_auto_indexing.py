import json
import unittest
from unittest.mock import patch

from src.api.agent_tool import commit_wiki_knowledge, retry_wiki_indexing
from src.api.exceptions import DatabaseException


class CommitAutoIndexingTests(unittest.TestCase):
    @patch("src.api.agent_tool.log_audit")
    @patch("src.api.agent_tool.run_wiki_indexing")
    @patch("src.api.agent_tool.create_knowledge_commit_runtime")
    def test_commit_queues_indexing_without_running_it_inline(
        self,
        runtime_factory,
        run_indexing,
        _log_audit,
    ):
        written_path = "qa/2026-07-15/example/example.md"
        runtime_factory.return_value.coordinator.commit.return_value = {
            "qa_file_path": written_path,
            "topic_file_path": None,
            "all_resources": [],
            "written_paths": [written_path],
            "details": "committed",
            "indexing": {
                "status": "queued", "indexed_files": [], "retry_targets": [],
                "queued_files": [written_path],
            },
        }
        response = json.loads(commit_wiki_knowledge(
            title="example",
            description="description",
            tags=[],
            content="content",
        ))

        self.assertTrue(response["success"])
        self.assertEqual(response["data"]["qa_file_path"], written_path)
        self.assertEqual(response["data"]["indexing"]["status"], "queued")
        self.assertEqual(response["data"]["indexing"]["queued_files"], [written_path])
        runtime_factory.return_value.coordinator.commit.assert_called_once()
        runtime_factory.return_value.close.assert_called_once_with()
        run_indexing.assert_not_called()

    @patch("src.api.agent_tool.log_audit")
    @patch("src.api.agent_tool.create_knowledge_commit_runtime")
    def test_queue_failure_reports_that_the_file_was_already_saved(
        self,
        runtime_factory,
        _log_audit,
    ):
        written_path = "qa/2026-07-15/example/example.md"
        runtime_factory.return_value.coordinator.commit.side_effect = DatabaseException(
            "지식 파일은 저장되었지만 인덱싱 작업 등록에 실패했습니다. "
            f"저장된 파일: {written_path}. 원인: queue unavailable"
        )

        response = json.loads(commit_wiki_knowledge(
            title="example",
            description="description",
            tags=[],
            content="content",
        ))

        self.assertFalse(response["success"])
        self.assertEqual(response["code"], "DATABASE_TRANSACTION_FAILED")
        self.assertIn("지식 파일은 저장되었지만", response["message"])
        self.assertIn(written_path, response["message"])
        runtime_factory.return_value.close.assert_called_once_with()

    @patch("src.api.agent_tool.run_wiki_indexing")
    @patch("src.settings.service.UserSettingsService")
    @patch("src.api.agent_tool.DatabaseManager")
    @patch("src.indexing.infrastructure.job_repository.IndexingJobRepository")
    def test_batch_retry_claims_and_completes_queued_jobs(
        self,
        job_repository_class,
        _database_manager,
        settings_service_class,
        run_indexing,
    ):
        paths = ["qa/one.md", "topics/Development/two.md"]
        repository = job_repository_class.return_value
        repository.claim.return_value = [
            {"owner_id": "USER_1", "file_path": path}
            for path in paths
        ]
        settings_service_class.return_value.get_runtime_config.return_value = {
            "openai_api_key": "stored-key",
            "storage": {"storage_type": "s3"},
        }
        run_indexing.return_value = json.dumps({
            "success": True,
            "message": "ok",
            "data": {"created": 2},
        })

        response = json.loads(retry_wiki_indexing(limit=20, force=True))

        self.assertTrue(response["success"])
        self.assertEqual(response["data"]["processed"], 2)
        repository.claim.assert_called_once_with(limit=20, force=True)
        repository.complete.assert_called_once_with(paths, owner_id="USER_1")

    @patch("src.api.agent_tool.run_wiki_indexing")
    @patch("src.settings.service.UserSettingsService")
    @patch("src.api.agent_tool.DatabaseManager")
    @patch("src.indexing.infrastructure.job_repository.IndexingJobRepository")
    def test_retry_groups_jobs_by_owner_and_loads_each_owner_settings(
        self,
        job_repository_class,
        _database_manager,
        settings_service_class,
        run_indexing,
    ):
        repository = job_repository_class.return_value
        repository.claim.return_value = [
            {"owner_id": "USER_1", "file_path": "qa/one.md"},
            {"owner_id": "USER_2", "file_path": "qa/two.md"},
        ]
        settings_service_class.return_value.get_runtime_config.side_effect = [
            {"openai_api_key": "user-1-key", "storage": {"storage_type": "s3"}},
            {"openai_api_key": "user-2-key", "storage": {"storage_type": "s3"}},
        ]

        observed_configs = []
        def run_for_owner(file_paths):
            from src.core.config import current_user_config
            observed_configs.append((file_paths, current_user_config.get().copy()))
            return json.dumps({"success": True, "data": {"created": 1}})
        run_indexing.side_effect = run_for_owner

        response = json.loads(retry_wiki_indexing(limit=20, force=True))

        self.assertTrue(response["success"])
        self.assertEqual(response["data"]["processed"], 2)
        self.assertEqual([config["user_id"] for _, config in observed_configs], ["USER_1", "USER_2"])
        self.assertEqual([config["openai_api_key"] for _, config in observed_configs], ["user-1-key", "user-2-key"])
        repository.complete.assert_any_call(["qa/one.md"], owner_id="USER_1")
        repository.complete.assert_any_call(["qa/two.md"], owner_id="USER_2")


if __name__ == "__main__":
    unittest.main()
