"""The coverage scoreboard's summary and regression diff, on tiny synthetic records."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from typing import Any

from zone_a.canonical_json import canonical_json


def _script() -> Any:
    spec = importlib.util.spec_from_file_location(
        "scoreboard", Path(__file__).parents[1] / "scripts" / "scoreboard.py"
    )
    assert spec is not None
    assert spec.loader is not None
    script = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(script)
    return script


def _file(
    sha: str, outcome: str, chars: int, carried_chars: int = 0, **more: Any
) -> dict[str, Any]:
    return {"sha256": sha, "document": "smpc", "section": None, "outcome": outcome} | {
        "chars": chars,
        "carriedChars": carried_chars,
        **more,
    }


def _section(sha: str, key: str, refused: list[str] | None = None, content: str = "c") -> Any:
    record = {"sha256": sha, "document": "smpc", "part": 0, "section": key, "chars": 10}
    if refused is None:
        return record | {"outcome": "carried", "contentSha256": content}
    return record | {"outcome": "refused", "code": refused[0].split(":")[0], "blockers": refused}


RECORDS = [
    _file("a", "whole", 100, 90, carried=2, sections=2),
    _section("a", "1"),
    _section("a", "2"),
    _file("b", "built-section-refused", 100, 50, carried=1, sections=2),
    _section("b", "1"),
    _section("b", "2", ["tab: a tab", "strike: x"]),
    _file("c", "built-section-refused", 100, 50, carried=1, sections=2),
    _section("c", "1"),
    _section("c", "2", ["tab: a tab"]),
    _file("d", "reader-refused", 200, code="tracked-change"),
    _file("e", "needs-a-person", 100, needs=["smpc.4.2"]),
]


def test_the_summary_is_counts_and_the_same_bytes_whatever_the_order() -> None:
    script = _script()
    summary = script.summarize(RECORDS)
    assert summary["buckets"] == {
        "whole": 1,
        "built-section-refused": 2,
        "document-refused": 0,
        "needs-a-person": 1,
        "parts-unclear": 0,
        "reader-refused": 1,
    }
    # Sections only of built files; characters of every file, the reader-refused one's as 0.
    assert summary["sections"] == {"carried": 4, "total": 6, "share": 0.6667}
    assert summary["characters"] == {"converted": 190, "total": 600, "coverage": 0.3167}
    assert summary["codes"]["reader"] == [["tracked-change", 1]]
    assert summary["codes"]["needs"] == [["smpc.4.2", 1]]
    assert summary["codes"]["section"] == [["tab", 2]]
    # Lifting tabs makes c whole; strikes then make b whole.
    assert summary["unlock"] == [["tab: a tab", 1], ["strike: x", 2]]
    assert canonical_json(script.summarize(list(reversed(RECORDS)))) == canonical_json(summary)


def test_a_cause_keeps_no_quoted_text_code_point_or_number() -> None:
    cause = _script().cause
    assert cause("computed-field", "a field 'Ibuprofen 400 mg' at 12") == (
        "computed-field: a field '…' at N"
    )
    assert cause("script", "U+00B5 raised or lowered") == "script: U+N raised or lowered"


def _board(folder: Path, records: list[dict[str, Any]]) -> Path:
    folder.mkdir()
    lines = "".join(canonical_json(r) + "\n" for r in records)
    (folder / "sections.jsonl").write_text(lines, encoding="utf-8")
    (folder / "summary.json").write_text(json.dumps({"versions": {"builder": folder.name}}))
    return folder


def test_the_regression_diff_lists_what_got_worse(tmp_path: Path) -> None:
    script = _script()
    before = _board(tmp_path / "before", RECORDS)
    after = [
        _file("a", "built-section-refused", 100, 40, carried=1, sections=2),
        _section("a", "1", content="other"),
        _section("a", "2", ["strike: x"]),
        # b is whole now: better, so not listed.
        _file("b", "whole", 100, 100, carried=2, sections=2),
        _section("b", "1"),
        _section("b", "2"),
        _file("c", "built-section-refused", 100, 50, carried=1, sections=2),
        _section("c", "1"),
        _section("c", "2", ["tab: a tab"]),
        _file("d", "reader-refused", 200, code="tracked-change"),
        _file("e", "reader-refused", 100, code="tab"),
    ]
    found = script.regress(before, _board(tmp_path / "after", after))
    assert found["versions"] == {"before": {"builder": "before"}, "after": {"builder": "after"}}
    assert [(f["sha256"], f["before"], f["after"]) for f in found["worseFiles"]] == [
        ("a", "whole", "built-section-refused"),
        ("e", "needs-a-person", "reader-refused"),
    ]
    assert [(s["sha256"], s["section"], s["change"]) for s in found["sections"]] == [
        ("a", "1", "changed"),
        ("a", "2", "refused: strike"),
    ]
    assert script.main(["regress", str(before), str(before)]) == 0
