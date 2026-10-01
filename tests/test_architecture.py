"""Enforce the hexagonal dependency rule: dependencies point inwards only."""

import ast
from pathlib import Path

import pytest

PKG = Path(__file__).resolve().parent.parent / "job_agent"
INFRA_LIBS = {"anthropic", "mcp", "httpx", "httpx2", "yaml", "markdown", "weasyprint", "pypdf", "subprocess"}


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text())
    found = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found |= {a.name for a in node.names}
        elif isinstance(node, ast.ImportFrom):
            prefix = "." * node.level
            found.add(prefix + (node.module or ""))
    return found


def _resolve(module: str, file: Path) -> str:
    """Absolute dotted name for an import (relative imports resolved against the file's package)."""
    if not module.startswith("."):
        return module
    level = len(module) - len(module.lstrip("."))
    package = file.relative_to(PKG.parent).with_suffix("").parts[:-1]
    base = package[: len(package) - (level - 1)]
    rest = module.lstrip(".")
    return ".".join([*base, *([rest] if rest else [])])


def _layer_files(layer: str):
    return sorted((PKG / layer).rglob("*.py"))


@pytest.mark.parametrize(
    "layer, forbidden_internal",
    [
        ("domain", ("job_agent.application", "job_agent.adapters", "job_agent.bootstrap", "job_agent.config", "job_agent.entrypoints")),
        ("application", ("job_agent.adapters", "job_agent.bootstrap", "job_agent.config", "job_agent.entrypoints")),
    ],
)
def test_core_layers_do_not_depend_on_outer_layers(layer, forbidden_internal):
    violations = []
    for file in _layer_files(layer):
        for imp in _imports(file):
            name = _resolve(imp, file)
            if name.startswith(forbidden_internal) or name.split(".")[0] in INFRA_LIBS:
                violations.append(f"{file.relative_to(PKG)} imports {name}")
    assert not violations, "\n".join(violations)


def test_adapters_do_not_depend_on_each_other_except_shared_llm_and_renderer():
    allowed = {"llm", "resume", "notifications"}  # shared Claude client, renderer, message formatting
    violations = []
    for file in _layer_files("adapters"):
        own = file.relative_to(PKG / "adapters").parts[0]
        for imp in _imports(file):
            name = _resolve(imp, file)
            if name.startswith("job_agent.adapters."):
                other = name.split(".")[2]
                if other != own and other not in allowed:
                    violations.append(f"{file.relative_to(PKG)} imports {name}")
            if name.startswith(("job_agent.bootstrap", "job_agent.entrypoints", "job_agent.config")):
                violations.append(f"{file.relative_to(PKG)} imports {name}")
    assert not violations, "\n".join(violations)
