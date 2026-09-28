"""The CSS cases the importer's T is held to hold the ePI reader's answers.

``scripts/generate_style_cases.py`` writes them; ``test/authority/style-cases.test.ts`` holds T.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"


def _script() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "generate_style_cases", SCRIPTS / "generate_style_cases.py"
    )
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_shared_style_cases_are_current() -> None:
    # test/authority/style-cases.test.ts holds T to them; they must follow any change here.
    script = _script()
    assert script.TARGET.read_text(encoding="utf-8") == script.render()


def test_a_recorded_divergence_must_be_one() -> None:
    script = _script()
    # Both readers read an unstyled span: recording that T refuses it would be no divergence
    # if it did not, and the generator refuses a record the reader's own answer contradicts.
    script.DIVERGENCES[("span", "")] = ("accepted", "x")
    with pytest.raises(ValueError, match="not a divergence"):
        script.render()


def test_a_divergence_for_a_case_that_is_gone_is_refused() -> None:
    script = _script()
    script.DIVERGENCES[("span", "color: purple")] = ("refused:contrast", "x")
    with pytest.raises(ValueError, match="not in CASES"):
        script.render()
