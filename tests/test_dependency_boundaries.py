"""Lightweight enforcement of the Stage 0 dependency direction."""

import ast
from pathlib import Path

import pytest

FORBIDDEN_IMPORTS = {
    "core": {
        "alembic",
        "fastapi",
        "ollama",
        "sqlalchemy",
        "satori.application",
        "satori.infrastructure",
    },
    "application": {
        "alembic",
        "fastapi",
        "ollama",
        "sqlalchemy",
        "satori.infrastructure",
    },
    "domain": {
        "alembic",
        "fastapi",
        "ollama",
        "pydantic",
        "sqlalchemy",
        "satori.application",
        "satori.infrastructure",
        "satori.observability",
    },
}


def imported_modules(path: Path) -> set[str]:
    """Collect absolute import targets from one Python module."""

    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
    return imported


def violates(module: str, forbidden: str) -> bool:
    """Match a module or any of its descendants."""

    return module == forbidden or module.startswith(f"{forbidden}.")


@pytest.mark.parametrize("layer", ["core", "domain", "application"])
def test_core_layers_do_not_import_frameworks_or_adapters(
    layer: str,
    project_root: Path,
) -> None:
    """Core-owned layers stay portable and point away from infrastructure."""

    failures: list[str] = []
    source_root = project_root / "src" / "satori" / layer
    for path in source_root.rglob("*.py"):
        for module in imported_modules(path):
            if any(violates(module, forbidden) for forbidden in FORBIDDEN_IMPORTS[layer]):
                failures.append(f"{path.relative_to(project_root)} imports {module}")

    assert failures == []


def test_stage_2_exposes_no_arbitrary_personality_or_value_setters(project_root: Path) -> None:
    """Read-only initial state has no public update/set/change API."""

    forbidden_prefixes = ("change_", "set_", "update_")
    failures: list[str] = []
    for relative in ("domain/personality.py", "domain/values.py"):
        path = project_root / "src" / "satori" / relative
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith(
                forbidden_prefixes
            ):
                failures.append(f"{relative}: {node.name}")

    assert failures == []
