"""Every change to what Word shows changes the result or is refused; nothing else changes it.

scripts/mutate.py edits the corpus documents one small change at a time. This runs two of each
kind of edit on every corpus document (a few thousand reads) and holds the reader to the rule: no
``miss`` (a page that changed and a result that did not) and no ``unstable`` (a page that did not
change and a result that did). What the reader does not report (font size) is held to
"unchanged", so its blind spots are known and cannot drift.
"""

from __future__ import annotations

import collections
from pathlib import Path

import pytest

from mutate import MUTATIONS, Mutation, outcomes

CORPUS = Path(__file__).resolve().parents[1] / "corpus"
DOCUMENTS = sorted(CORPUS.glob("*/*.docx"))


@pytest.fixture(scope="module")
def verdicts() -> dict[str, collections.Counter[str]]:
    table: dict[str, collections.Counter[str]] = collections.defaultdict(collections.Counter)
    for path in DOCUMENTS:
        for name, _, verdict in outcomes(path.read_bytes(), 2):
            table[name][verdict] += 1
    return table


@pytest.mark.parametrize("mutation", MUTATIONS, ids=lambda m: m.name)
def test_no_change_goes_unnoticed_and_nothing_else_changes_the_result(
    mutation: Mutation, verdicts: dict[str, collections.Counter[str]]
) -> None:
    name = mutation.name
    counts = verdicts[name]
    # Every kind of edit finds places to make it in the corpus.
    assert sum(counts.values()) >= 10, f"{name}: too few sites ({dict(counts)})"
    assert counts["miss"] == 0, f"{name}: {counts['miss']} changes went unnoticed"
    assert counts["unstable"] == 0, f"{name}: {counts['unstable']} unchanged pages read differently"
