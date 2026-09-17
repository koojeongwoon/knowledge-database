import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock, patch

from src.core.config import current_user_config
from src.retrieval.blind_dataset import GeneratedQuestion, GeneratedQuestionBatch, generate_blind_dataset


def test_blind_dataset_generation_uses_broker_without_provider_credentials():
    settings = Mock()
    settings.get_llm_preferences.return_value = {
        "model": "gpt-5.6-luna", "auth_type": "openai_oauth",
    }
    chat = Mock()
    chat.parse.side_effect = [
        GeneratedQuestionBatch(questions=[GeneratedQuestion(
            case_id="blind-answer-001", query="검증 질문", query_type="exact",
        )]),
        GeneratedQuestionBatch(questions=[GeneratedQuestion(
            case_id="blind-no-answer-001", query="없는 질문", query_type="no-answer",
        )]),
    ]
    documents = [{
        "file_path": "qa/example.md", "title": "Example", "description": "Description",
        "parent_content": "Content",
    }]

    with TemporaryDirectory() as directory, \
         patch("src.retrieval.blind_dataset.load_evaluation_cases", return_value=[]), \
         patch("src.retrieval.blind_dataset._sample_documents", return_value=documents), \
         patch("src.settings.service.UserSettingsService", return_value=settings), \
         patch("src.indexing.infrastructure.broker_chat.BrokerStructuredChat", return_value=chat) as broker:
        token = current_user_config.set({"user_id": "owner-1"})
        try:
            result = generate_blind_dataset(
                Mock(), str(Path(directory) / "development.json"),
                str(Path(directory) / "queries.json"), str(Path(directory) / "answers.json"),
                answer_cases=1, no_answer_cases=1,
            )
        finally:
            current_user_config.reset(token)

        queries = json.loads((Path(directory) / "queries.json").read_text())
        answers = json.loads((Path(directory) / "answers.json").read_text())

    broker.assert_called_once_with("owner-1", auth_type="openai_oauth")
    assert chat.parse.call_count == 2
    assert all(call.kwargs["model"] == "gpt-5.6-luna" for call in chat.parse.call_args_list)
    assert result["cases"] == 2
    assert {item["id"] for item in queries["cases"]} == {"blind-answer-001", "blind-no-answer-001"}
    assert answers["answers"][0]["expected_paths"] == ["qa/example.md"]
