"""The rule that narrative never reaches a log, enforced on this package's own source.

The repository's first non-negotiable rule is that clinical text is never logged. The golden
vectors and contract fixtures are synthetic, but this package also reads real, authority-published
labels (``labels/ema-epi/``, the QRD check and the ePI reader's tests, under the scoped exception
in ``AGENTS.md``) and the EMA's templates, so a test that printed what it read would put an SmPC's
text in a CI log. Three checks:

1. no test may print, log, warn or write to standard output or error at all, however it reaches
   them (``sys.stdout.write``, ``from sys import stdout``, ``pprint``, ``warnings``,
   ``logging``), so a future test cannot put label or vector text there;
2. no test compares a whole check result as text, which a failing assertion would print (the
   committed results are compared by digest, and differences named by JSON pointer);
3. no Python or Markdown file in ``zone-a/`` may contain a run of text taken from the vectors,
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

FORBIDDEN_CALLS = {"print", "breakpoint", "pprint", "pp"}
# Modules that write to a stream or a log: importing one at all is refused.
FORBIDDEN_MODULES = {"logging", "pprint", "warnings", "faulthandler", "traceback"}
# The process's own streams, however they are reached.
STREAMS = {"stdout", "stderr", "__stdout__", "__stderr__"}


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


def _violations(source: str) -> list[str]:
    """What in a test module could print, log or warn, by the rules in the module docstring."""
    out: list[str] = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            if node.func.id in FORBIDDEN_CALLS:
                out.append(f"calls {node.func.id}()")
        elif isinstance(node, ast.Attribute):
            # Any reference to a stream, called or not and however deep: sys.stdout.write,
            # sys.stderr, os.sys.stdout, an alias's .stdout.
            if node.attr in STREAMS:
                out.append(f"reaches .{node.attr}")
            if node.attr in FORBIDDEN_CALLS:
                out.append(f"calls .{node.attr}")
        elif isinstance(node, ast.Name) and node.id in STREAMS:
            out.append(f"names {node.id}")
        elif isinstance(node, ast.Import):
            out.extend(
                f"imports {alias.name}"
                for alias in node.names
                if alias.name.split(".")[0] in FORBIDDEN_MODULES
            )
        elif isinstance(node, ast.ImportFrom):
            if node.module is not None and node.module.split(".")[0] in FORBIDDEN_MODULES:
                out.append(f"imports from {node.module}")
            out.extend(
                f"imports {alias.name}"
                for alias in node.names
                if alias.name in STREAMS or alias.name in FORBIDDEN_CALLS
            )
    return out


@pytest.mark.parametrize("path", sorted(TESTS.glob("*.py")), ids=lambda path: path.name)
def test_no_test_module_prints_or_logs(path: Path) -> None:
    assert _violations(path.read_text(encoding="utf-8")) == [], path.name


@pytest.mark.parametrize(
    "source",
    [
        "print('x')",
        "import sys\nsys.stdout.write('x')",
        "import sys\nsys.stderr.write('x')",
        "import sys as s\ns.stdout.write('x')",
        "import sys\nout = sys.stdout\nout.write('x')",
        "from sys import stdout\nstdout.write('x')",
        "from sys import stderr as e\ne.write('x')",
        "import sys\nsys.__stdout__.write('x')",
        "import pprint\npprint.pprint('x')",
        "from pprint import pprint\npprint('x')",
        "import warnings\nwarnings.warn('x')",
        "from warnings import warn\nwarn('x')",
        "import logging\nlogging.info('x')",
        "from logging import getLogger",
        "import traceback\ntraceback.print_exc()",
        "breakpoint()",
    ],
)
def test_every_way_to_print_is_caught(source: str) -> None:
    assert _violations(source), source


def test_no_test_compares_a_committed_result_as_text() -> None:
    # A failing `assert text == expected` prints both texts: a whole check result holds excerpts
    # of the label (test_qrd_check.py compares digests and names the JSON pointers that differ).
    source = (TESTS / "test_qrd_check.py").read_text(encoding="utf-8")
    assert 'read_text(encoding="utf-8") == content' not in source
    assert "_differing_pointers" in source


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
