"""docs/requirements.md traces every requirement to tests that exist, and every test to one."""

from __future__ import annotations

import ast
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ROW = re.compile(r"^\| (R-\d\d) \| (.+?) \| (.+?) \|$")
EVIDENCE = re.compile(r"`(test_\w+\.py)::(test_\w+)`")


def _trace() -> dict[str, set[tuple[str, str]]]:
    rows = [
        ROW.match(line) for line in (ROOT / "docs" / "requirements.md").read_text().splitlines()
    ]
    return {row.group(1): set(EVIDENCE.findall(row.group(3))) for row in rows if row}


def _tests() -> set[tuple[str, str]]:
    found: set[tuple[str, str]] = set()
    for path in (ROOT / "tests").glob("test_*.py"):
        for node in ast.parse(path.read_text()).body:
            if isinstance(node, ast.FunctionDef) and node.name.startswith("test_"):
                found.add((path.name, node.name))
    return found


def test_every_requirement_names_tests_that_exist() -> None:
    trace, tests = _trace(), _tests()
    assert len(trace) >= 17
    for requirement, evidence in trace.items():
        assert evidence, f"{requirement} names no test"
        missing = sorted(f"{f}::{t}" for f, t in evidence - tests)
        assert not missing, f"{requirement} names tests that do not exist: {missing}"


def test_every_test_proves_a_requirement() -> None:
    traced = set().union(*_trace().values())
    untraced = sorted(f"{f}::{t}" for f, t in _tests() - traced if f != "test_traceability.py")
    assert not untraced, f"tests that prove no requirement in docs/requirements.md: {untraced}"
