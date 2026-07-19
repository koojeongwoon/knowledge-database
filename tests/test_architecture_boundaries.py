import ast
from pathlib import Path


def test_indexing_application_does_not_import_concrete_adapters() -> None:
    source_path = Path("src/indexing/application/service.py")
    tree = ast.parse(source_path.read_text(encoding="utf-8"), filename=str(source_path))
    imported_modules = {
        node.module
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module
    }

    forbidden = {
        "src.core.database.factory",
        "src.core.storage.factory",
        "src.core.config",
        "src.indexing.infrastructure.repository",
        "src.indexing.infrastructure.expansion",
        "src.ontology.repository",
        "src.ontology.service",
        "src.ontology.composition",
        "openai",
        "concurrent.futures",
    }
    assert imported_modules.isdisjoint(forbidden)

    method_names = {
        node.name for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    assert "_process_parallel" not in method_names

    direct_print_calls = {
        node.func.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "print"
    }
    assert not direct_print_calls


def test_settings_auth_router_is_wired_by_factory() -> None:
    source_path = Path("src/settings/web_auth.py")
    tree = ast.parse(source_path.read_text(encoding="utf-8"), filename=str(source_path))
    imported_modules = {
        node.module for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module
    }
    assert "src.settings.oauth_session" in imported_modules
    assert "src.settings.web" not in imported_modules
    assert "src.core.config" not in imported_modules


def test_settings_content_router_receives_auth_and_services() -> None:
    source_path = Path("src/settings/web_content.py")
    tree = ast.parse(source_path.read_text(encoding="utf-8"), filename=str(source_path))
    imported_modules = {
        node.module for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module
    }
    assert "src.settings.web" not in imported_modules
    assert "src.settings.inbox" not in imported_modules
    assert "src.settings.documents" not in imported_modules
    assert "src.core.storage.factory" not in imported_modules


def test_settings_learning_and_feedback_routers_receive_services() -> None:
    for source_path in (Path("src/settings/web_learning.py"), Path("src/settings/web_feedback.py")):
        tree = ast.parse(source_path.read_text(encoding="utf-8"), filename=str(source_path))
        imported_modules = {
            node.module for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) and node.module
        }
        assert "src.settings.web" not in imported_modules
        assert "src.core.database.factory" not in imported_modules
        assert "src.retrieval.feedback" not in imported_modules
        assert "src.learning.infrastructure.dashboard_repository" not in imported_modules


def test_settings_configuration_and_key_routers_receive_services() -> None:
    for source_path in (Path("src/settings/web_configuration.py"), Path("src/settings/web_api_keys.py")):
        tree = ast.parse(source_path.read_text(encoding="utf-8"), filename=str(source_path))
        imported_modules = {
            node.module for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) and node.module
        }
        assert "src.settings.web" not in imported_modules
        assert "src.settings.service" not in imported_modules
        assert "src.api_keys.service" not in imported_modules
        assert "src.core.storage.factory" not in imported_modules


def test_settings_dispatcher_is_independent_from_web_composition() -> None:
    source_path = Path("src/settings/web_dispatcher.py")
    tree = ast.parse(source_path.read_text(encoding="utf-8"), filename=str(source_path))
    imported_modules = {
        node.module for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module
    }
    assert "src.settings.web" not in imported_modules
    assert not any(module.startswith("src.settings") for module in imported_modules)


def test_entrypoints_delegate_indexer_wiring_to_composition_root() -> None:
    forbidden = {
        "src.core.storage.factory",
        "src.indexing.application.expansion_executor",
        "src.indexing.application.file_executor",
        "src.indexing.application.inventory_collector",
        "src.indexing.application.service",
        "src.indexing.infrastructure.expansion",
        "src.indexing.infrastructure.repository",
        "src.ontology.composition",
    }

    for source_path in (Path("main.py"), Path("src/api/agent_tool.py")):
        tree = ast.parse(source_path.read_text(encoding="utf-8"), filename=str(source_path))
        imported_modules = {
            node.module
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) and node.module
        }
        assert imported_modules.isdisjoint(forbidden)
        assert "src.indexing.composition" in imported_modules


def test_retrieval_application_does_not_import_concrete_adapters_or_config() -> None:
    source_path = Path("src/retrieval/application/service.py")
    tree = ast.parse(source_path.read_text(encoding="utf-8"), filename=str(source_path))
    imported_modules = {
        node.module
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module
    }
    forbidden = {
        "src.core.config",
        "src.core.database.factory",
        "src.indexing.domain.embedding",
        "src.retrieval.infrastructure.repository",
        "src.retrieval.infrastructure.retrieval_repository",
        "src.retrieval.infrastructure.reranker",
        "sentence_transformers",
    }
    assert imported_modules.isdisjoint(forbidden)
    assert not any(
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "print"
        for node in ast.walk(tree)
    )


def test_entrypoints_delegate_retrieval_wiring_to_composition_root() -> None:
    for source_path in (Path("main.py"), Path("src/api/agent_tool.py")):
        tree = ast.parse(source_path.read_text(encoding="utf-8"), filename=str(source_path))
        imported_modules = {
            node.module
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) and node.module
        }
        assert "src.retrieval.composition" in imported_modules
        assert "src.retrieval.application.service" not in imported_modules
        assert "src.retrieval.infrastructure.repository" not in imported_modules


def test_knowledge_commit_application_does_not_construct_infrastructure() -> None:
    source_path = Path("src/wiki/application/integration.py")
    tree = ast.parse(source_path.read_text(encoding="utf-8"), filename=str(source_path))
    imported_modules = {
        node.module
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module
    }
    forbidden = {
        "src.core.database.factory",
        "src.core.storage.factory",
        "src.indexing.infrastructure.repository",
        "src.indexing.infrastructure.job_repository",
    }
    assert imported_modules.isdisjoint(forbidden)


def test_agent_tool_delegates_knowledge_commit_wiring_to_composition() -> None:
    source_path = Path("src/api/agent_tool.py")
    tree = ast.parse(source_path.read_text(encoding="utf-8"), filename=str(source_path))
    imported_modules = {
        node.module
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module
    }
    assert "src.wiki.composition" in imported_modules
    assert "src.wiki.application.integration" not in imported_modules


def test_learning_application_delegates_rules_and_does_not_construct_infrastructure() -> None:
    source_path = Path("src/learning/application/session_service.py")
    tree = ast.parse(source_path.read_text(encoding="utf-8"), filename=str(source_path))
    imported_modules = {
        node.module
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module
    }
    assert imported_modules.isdisjoint({
        "src.core.database.factory",
        "src.learning.infrastructure.repository",
        "src.learning.infrastructure.identity",
    })
    assert "src.learning.domain.session" in imported_modules


def test_agent_tool_delegates_learning_wiring_to_composition() -> None:
    source_path = Path("src/api/agent_tool.py")
    tree = ast.parse(source_path.read_text(encoding="utf-8"), filename=str(source_path))
    imported_modules = {
        node.module
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module
    }
    assert "src.learning.composition" in imported_modules
    assert "src.learning.infrastructure.repository" not in imported_modules


def test_learning_candidate_commit_lives_behind_internal_api_handler() -> None:
    facade_path = Path("src/api/agent_tool.py")
    facade_tree = ast.parse(facade_path.read_text(encoding="utf-8"), filename=str(facade_path))
    imported_modules = {
        node.module
        for node in ast.walk(facade_tree)
        if isinstance(node, ast.ImportFrom) and node.module
    }
    assert "src.api.handlers.learning" in imported_modules

    handler_path = Path("src/api/handlers/learning.py")
    handler_tree = ast.parse(handler_path.read_text(encoding="utf-8"), filename=str(handler_path))
    handler_imports = {
        node.module
        for node in ast.walk(handler_tree)
        if isinstance(node, ast.ImportFrom) and node.module
    }
    assert "src.api.decorators" not in handler_imports
    assert "src.learning.infrastructure.repository" not in handler_imports


def test_retrieval_execution_lives_behind_internal_api_handler() -> None:
    facade_path = Path("src/api/agent_tool.py")
    facade_tree = ast.parse(facade_path.read_text(encoding="utf-8"), filename=str(facade_path))
    imported_modules = {
        node.module
        for node in ast.walk(facade_tree)
        if isinstance(node, ast.ImportFrom) and node.module
    }
    assert "src.api.handlers.retrieval" in imported_modules

    handler_path = Path("src/api/handlers/retrieval.py")
    handler_tree = ast.parse(handler_path.read_text(encoding="utf-8"), filename=str(handler_path))
    handler_imports = {
        node.module
        for node in ast.walk(handler_tree)
        if isinstance(node, ast.ImportFrom) and node.module
    }
    assert "src.api.decorators" not in handler_imports
    assert "src.retrieval.infrastructure.retrieval_repository" not in handler_imports


def test_knowledge_commit_execution_lives_behind_internal_api_handler() -> None:
    facade_path = Path("src/api/agent_tool.py")
    facade_tree = ast.parse(facade_path.read_text(encoding="utf-8"), filename=str(facade_path))
    imported_modules = {
        node.module for node in ast.walk(facade_tree)
        if isinstance(node, ast.ImportFrom) and node.module
    }
    assert "src.api.handlers.knowledge" in imported_modules

    handler_path = Path("src/api/handlers/knowledge.py")
    handler_tree = ast.parse(handler_path.read_text(encoding="utf-8"), filename=str(handler_path))
    handler_imports = {
        node.module for node in ast.walk(handler_tree)
        if isinstance(node, ast.ImportFrom) and node.module
    }
    assert "src.api.decorators" not in handler_imports
    assert "src.wiki.composition" not in handler_imports


def test_indexing_execution_lives_behind_internal_api_handler() -> None:
    facade_path = Path("src/api/agent_tool.py")
    facade_tree = ast.parse(facade_path.read_text(encoding="utf-8"), filename=str(facade_path))
    imported_modules = {
        node.module for node in ast.walk(facade_tree)
        if isinstance(node, ast.ImportFrom) and node.module
    }
    assert "src.api.handlers.indexing" in imported_modules

    handler_path = Path("src/api/handlers/indexing.py")
    handler_tree = ast.parse(handler_path.read_text(encoding="utf-8"), filename=str(handler_path))
    handler_imports = {
        node.module for node in ast.walk(handler_tree)
        if isinstance(node, ast.ImportFrom) and node.module
    }
    assert "src.api.decorators" not in handler_imports
    assert "src.indexing.composition" not in handler_imports


def test_settings_page_router_does_not_import_application_services() -> None:
    source_path = Path("src/settings/web_pages.py")
    tree = ast.parse(source_path.read_text(encoding="utf-8"), filename=str(source_path))
    imported_modules = {
        node.module for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module
    }
    assert imported_modules.isdisjoint({
        "src.settings.service", "src.settings.inbox", "src.settings.documents",
        "src.retrieval.feedback", "src.learning.application.dashboard",
    })
