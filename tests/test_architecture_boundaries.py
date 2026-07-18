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
