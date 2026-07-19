import inspect

from src.retrieval.infrastructure.retrieval_repository import PostgresRetrievalRepository


def test_every_live_retrieval_path_explicitly_excludes_baseline_directories():
    source = inspect.getsource(PostgresRetrievalRepository)

    assert source.count("d.file_path NOT LIKE 'baselines/%%'") >= 2
    assert "source_path NOT LIKE 'baselines/%%'" in source
    assert "file_path NOT LIKE 'baselines/%%'" in source
    assert source.count("baseline-drafts/%%") >= 4
