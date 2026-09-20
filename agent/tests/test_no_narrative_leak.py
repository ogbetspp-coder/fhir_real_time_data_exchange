"""The rule that narrative never reaches a log, enforced on this package's own source.

The same check Zone A runs, narrowed to what this deployable can leak. Three parts:

1. **Nothing under ``src/`` prints or logs.** The agent handles narrative for a living — a
   ``get_section`` result *is* clinical text — so the one writer in the package is
   ``audit.emit``, which writes a record it has already scanned for forbidden keys. Every other
   module is forbidden ``print``, ``sys.stdout``/``sys.stderr`` and ``logging`` outright.
2. **No test prints or logs either**, so a failing assertion can never put a fixture's
   narrative on a CI console.
3. **No Python or Markdown file here quotes the fixture's narrative.** The fixture is
   synthetic, which is exactly why the habit is worth keeping while it costs nothing.
"""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Any

import pytest

from .fake_query_service import load_sections

AGENT_ROOT = Path(__file__).resolve().parents[1]
SOURCE = AGENT_ROOT / "src"
TESTS = AGENT_ROOT / "tests"

# The one module allowed to write to a stream, because writing the audit record is its purpose.
AUDIT_MODULE = SOURCE / "verifiable_answer_agent" / "audit.py"

FORBIDDEN_CALLS = {"print", "breakpoint"}
FORBIDDEN_MODULES = {"logging"}

MINIMUM_QUOTED_RUN = 24


def _is_project_file(path: Path) -> bool:
    return not any(part.startswith(".") for part in path.relative_to(AGENT_ROOT).parts)


def _python_files(root: Path) -> list[Path]:
    return sorted(path for path in root.rglob("*.py") if _is_project_file(path))


def _writes_to_a_stream(node: ast.AST) -> bool:
    return (
        isinstance(node, ast.Attribute)
        and isinstance(node.value, ast.Name)
        and node.value.id == "sys"
        and node.attr in ("stdout", "stderr")
    )


def _offences(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    found: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            function = node.func
            if isinstance(function, ast.Name) and function.id in FORBIDDEN_CALLS:
                found.append(f"calls {function.id}()")
        if _writes_to_a_stream(node):
            found.append(f"writes to sys.{node.attr}")  # type: ignore[attr-defined]
        if isinstance(node, ast.Import):
            found.extend(
                f"imports {alias.name}"
                for alias in node.names
                if alias.name.split(".")[0] in FORBIDDEN_MODULES
            )
        if (
            isinstance(node, ast.ImportFrom)
            and node.module is not None
            and node.module.split(".")[0] in FORBIDDEN_MODULES
        ):
            found.append(f"imports from {node.module}")
    return found


@pytest.mark.parametrize(
    "path", _python_files(SOURCE), ids=lambda path: str(path.relative_to(AGENT_ROOT))
)
def test_no_source_module_prints_or_logs(path: Path) -> None:
    offences = _offences(path)
    if path == AUDIT_MODULE:
        # audit.py may hold a reference to sys.stdout as the default destination, and nothing
        # else: no print, no logging, no write to stderr.
        assert offences == ["writes to sys.stdout"], offences
        return
    assert not offences, f"{path.relative_to(AGENT_ROOT)}: {'; '.join(offences)}"


@pytest.mark.parametrize(
    "path", _python_files(TESTS), ids=lambda path: str(path.relative_to(AGENT_ROOT))
)
def test_no_test_module_prints_or_logs(path: Path) -> None:
    offences = _offences(path)
    assert not offences, f"{path.relative_to(AGENT_ROOT)}: {'; '.join(offences)}"


def _quotable() -> set[str]:
    quotable: set[str] = set()
    for section in load_sections().values():
        for candidate in (section.payload["div"], section.payload["text"]):
            value: Any = candidate
            if isinstance(value, str) and len(value) >= MINIMUM_QUOTED_RUN:
                quotable.add(value)
    return quotable


def test_no_file_here_quotes_the_fixtures_narrative() -> None:
    quotable = _quotable()
    assert quotable, "the fixture carries no narrative long enough to check for"
    markdown = [path for path in AGENT_ROOT.rglob("*.md") if _is_project_file(path)]
    files = [*_python_files(AGENT_ROOT), *markdown]
    offenders = [
        f"{path.relative_to(AGENT_ROOT)} quotes a {len(quoted)}-character run of fixture narrative"
        for path in files
        for quoted in quotable
        if quoted in path.read_text(encoding="utf-8")
    ]
    assert not offenders, "\n".join(sorted(offenders))
