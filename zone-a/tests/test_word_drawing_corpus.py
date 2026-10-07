"""The corpus harness of the Word drawing's step 1 (``scripts/word_drawing_corpus.py``)."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from types import ModuleType

import pytest
from label_docx import browser

ROOT = Path(__file__).resolve().parents[2]
COMMITTED = ROOT / "test" / "fixtures" / "certified-word" / "recompute"


def _harness() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "word_drawing_corpus", ROOT / "zone-a" / "scripts" / "word_drawing_corpus.py"
    )
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_window_launcher_runs_the_shell_at_375_by_812_and_a_ratio_of_2(tmp_path: Path) -> None:
    launcher = _harness().window_launcher(Path("/opt/shell"), tmp_path)
    assert launcher.read_text("utf-8") == (
        '#!/bin/sh\nexec "/opt/shell" --window-size=375,812 --force-device-scale-factor=2 "$@"\n'
    )
    assert launcher.stat().st_mode & 0o111 == 0o111


def _part(**fields: object) -> dict[str, object]:
    same = {
        f"{w}_shell_eq_{c}": True
        for w in ("verdicts", "raw")
        for c in ("google", "shell2", "window")
    }
    return {
        "builds": True,
        "drawn": 2,
        "everySection": False,
        "agree": {"google": 2, "shell": 2, "shell2": 2, "window": 2},
        "differ": [],
        "parse_shell_same": 2,
        "parse_google_same": 2,
        **same,
        **fields,
    }


def test_the_summary_counts_parts_not_files(tmp_path: Path) -> None:
    lines = [
        {
            "document": "smpc",
            "index": 0,
            "outcome": "read",
            "parts": [_part(), _part(builds=False)],
        },
        {
            "document": "smpc",
            "index": 1,
            "outcome": "read",
            "parts": [_part(raw_shell_eq_google=False)],
        },
        {
            "document": "smpc",
            "index": 2,
            "outcome": "read",
            "parts": [_part(documentRefused="frame")],
        },
        {"document": "smpc", "index": 3, "outcome": "reader-refused", "code": "symbol-font"},
        {"label": 0, "runs": 40, "statuses": [0], "outputs": 1},
        {"label": 1, "runs": 40, "statuses": [0, 1], "outputs": 2},
    ]
    results = tmp_path / "lines.jsonl"
    results.write_text("".join(json.dumps(line) + "\n" for line in lines), "utf-8")
    counted = _harness().summary([results])
    assert counted["smpc"]["files"] == 4
    assert counted["smpc"]["build"] == 2
    assert counted["smpc"]["readyButDocumentRefused"] == 1
    assert counted["smpc"]["sections"] == 4
    assert counted["smpc"]["same"]["raw_shell_eq_google"] == 1
    assert counted["smpc"]["same"]["verdicts_shell_eq_google"] == 2
    assert counted["repeat"] == {
        "labels": 2,
        "runs": 80,
        "allExitZero": False,
        "oneOutputEach": False,
    }


def test_it_repeats_every_label_the_drawing_can_sign(tmp_path: Path) -> None:
    harness = _harness()
    folder = tmp_path / "labels"
    folder.mkdir()
    for name in ("b.docx", "a.docx"):
        (folder / name).write_bytes(b"")
    line = {
        "document": "pl",
        "folder": str(folder),
        "index": 1,
        "view": "accepted",
        "parts": [{"part": 0, "everySection": False}, {"part": 1, "everySection": True}],
    }
    results = tmp_path / "pl.jsonl"
    results.write_text(json.dumps(line) + "\n", "utf-8")
    signable = harness._signable([results])
    committed = [path.stem for kind, path, _ in signable if kind == "committed"]
    assert committed == ["smpc", "smpc-tracked", "smpc-assigned", "pl"]
    ((kind, path, asked),) = [entry for entry in signable if entry[0] == "corpus"]
    assert (kind, path.name) == ("corpus", "b.docx")
    assert {k: asked[k] for k in ("document", "view", "part", "assignments")} == {
        "document": "pl",
        "view": "accepted",
        "part": 1,
        "assignments": {},
    }


@pytest.mark.skipif(browser.find_chrome() is None, reason="Chrome is not installed")
def test_one_label_is_measured_alike_and_named_by_nothing_it_says() -> None:
    label = COMMITTED / "pl.docx"
    line = _harness().one("pl", label, browser.CHROME, browser.CHROME)
    (part,) = line["parts"]
    assert part["builds"]
    assert part["everySection"]
    assert part["drawn"] == part["parse_shell_same"] == part["parse_google_same"] > 0
    assert all(part[k] is True for k in part if k.startswith(("verdicts_", "raw_")))
    assert set(part["agree"].values()) == {part["drawn"]}
    written = json.dumps(line)
    narratives = [
        s["narrative"] for s in json.loads((COMMITTED / "pl.json").read_bytes())["sections"]
    ]
    assert not any(n[60:120] in written for n in narratives if n)
