"""The rule that narrative never reaches a log, enforced on this package's own source.

The repository's first non-negotiable rule is that clinical text is never logged. The vectors
and fixtures here are synthetic, so nothing in this directory could leak a real label — which is
exactly why the habit has to be enforced mechanically now, while the cost of keeping it is zero.
Two checks:

1. no test may print or log at all, so a future test cannot put vector text on stdout;
2. no Python or Markdown file in ``zone-a/`` may contain a run of text taken from the vectors,
   which is what stops a README or a docstring from quoting a sentence to illustrate a case.
"""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Any

import pytest

from .conftest import VECTORS_PATH, load_json

ZONE_A = Path(__file__).resolve().parents[1]
TESTS = ZONE_A / "tests"

# Long enough that a match is a quotation rather than a coincidence of common words, short
# enough to catch a fragment of a sentence.
MINIMUM_QUOTED_RUN = 24

FORBIDDEN_CALLS = {"print", "breakpoint"}
FORBIDDEN_MODULES = {"logging"}


def _is_project_file(path: Path) -> bool:
    """Exclude every dot-directory: the two virtualenvs and the tool caches are not ours.

    ``.venv`` holds the dependencies and ``.uv-bootstrap`` holds ``uv`` itself, installed
    outside the project environment because ``uv sync --frozen`` prunes it from ``.venv``.
    Both are full of third-party Python that this check has no business reading.
    """
    return not any(part.startswith(".") for part in path.relative_to(ZONE_A).parts)


def _python_files() -> list[Path]:
    return [
        path
        for path in ZONE_A.rglob("*.py")
        if _is_project_file(path) and "contracts" not in path.parts
    ]


def _text_files() -> list[Path]:
    return [*_python_files(), *(path for path in ZONE_A.rglob("*.md") if _is_project_file(path))]


def _add(value: Any, into: set[str]) -> None:
    if isinstance(value, str) and len(value) >= MINIMUM_QUOTED_RUN:
        into.add(value)


def _narrative_bearing_strings(vectors: Any) -> set[str]:
    """Only the fields that carry sentences.

    A report's ``issues`` entries and reason codes are the verifier's own vocabulary and are
    quoted in this implementation on purpose; the narrative lives in the normalisation and XHTML
    inputs and outputs, and in each verify case's page text and section markup.
    """
    quotable: set[str] = set()
    for case in [*vectors["normalization"], *vectors["xhtml"]]:
        _add(case["input"], quotable)
        _add(case["expected"], quotable)
    for case in vectors["verify"]:
        for page in case["input"]["source"]["pages"]:
            _add(page["text"], quotable)
        for section in case["input"]["sections"]:
            _add(section["div"], quotable)
    return quotable


@pytest.mark.parametrize("path", sorted(TESTS.glob("*.py")), ids=lambda path: path.name)
def test_no_test_module_prints_or_logs(path: Path) -> None:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            function = node.func
            assert not (isinstance(function, ast.Name) and function.id in FORBIDDEN_CALLS), (
                f"{path.name} calls {getattr(function, 'id', '?')}()"
            )
            assert not (
                isinstance(function, ast.Attribute)
                and isinstance(function.value, ast.Name)
                and function.value.id == "sys"
                and function.attr in ("stdout", "stderr")
            ), f"{path.name} writes to sys.{function.attr}"
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert alias.name.split(".")[0] not in FORBIDDEN_MODULES, path.name
        if isinstance(node, ast.ImportFrom) and node.module is not None:
            assert node.module.split(".")[0] not in FORBIDDEN_MODULES, path.name


def test_no_source_file_quotes_a_vector() -> None:
    quotable = _narrative_bearing_strings(load_json(VECTORS_PATH))
    assert quotable, "the vectors carry no strings long enough to check for"
    offenders: list[str] = []
    for path in _text_files():
        content = path.read_text(encoding="utf-8")
        offenders.extend(
            f"{path.relative_to(ZONE_A)} quotes a {len(quoted)}-character vector string"
            for quoted in quotable
            if quoted in content
        )
    assert not offenders, "\n".join(sorted(offenders))
