"""The coverage scoreboard's summary and regression diff, on tiny synthetic records."""

from __future__ import annotations

import importlib.util
import io
import json
import zipfile
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
    sha: str, outcome: str, chars: int | None, carried_chars: int = 0, **more: Any
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
    _section("b", "2", ["tab: a tab", "formatting: strike"]),
    _file("c", "built-section-refused", 100, 50, carried=1, sections=2),
    _section("c", "1"),
    _section("c", "2", ["tab: a tab"]),
    _file("d", "reader-refused", 200, code="tracked-change"),
    _file("e", "needs-a-person", 100, needs=["smpc.4.2"]),
    _file("f", "reader-refused", None, code="invalid-package"),
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
        "reader-refused": 2,
    }
    # Sections only of built files; characters of every file, the reader-refused one's as 0
    # converted, and one with no text to count apart.
    assert summary["sections"] == {"carried": 4, "total": 6, "share": 0.6667}
    assert summary["characters"] == {
        "converted": 190,
        "total": 600,
        "coverage": 0.3167,
        "unmeasurable": 1,
    }
    assert summary["codes"]["reader"] == [["invalid-package", 1], ["tracked-change", 1]]
    assert summary["codes"]["needs"] == [["smpc.4.2", 1]]
    assert summary["codes"]["section"] == [["tab", 2]]
    # Lifting tabs makes c whole; strikes then make b whole.
    assert summary["unlock"] == [
        {"cause": "tab: a tab", "newlyWhole": 1},
        {"cause": "formatting: strike", "newlyWhole": 2},
    ]
    assert canonical_json(script.summarize(list(reversed(RECORDS)))) == canonical_json(summary)


def test_a_cause_keeps_no_quoted_text_code_point_or_number() -> None:
    script = _script()
    assert script.cause("computed-field", "a field 'Ibuprofen 400 mg' at 12") == (
        "computed-field: a field '…' at N"
    )
    found = script.cause("script", "U+00B5 raised or lowered")
    assert found == "script: U+N raised or lowered"
    # A summary shows only the builder's known forms; any other detail is "other".
    assert script.public(found) == found
    assert script.public("formatting: position+N-sizeN-inN") == "formatting: position+N-sizeN-inN"
    assert script.public("tab: Take one tablet daily") == "tab: other"
    assert script.public("formatting: two words") == "formatting: other"


def _docx(body: str | None) -> bytes:
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as package:
        if body is not None:
            package.writestr(
                "word/document.xml", f"<w:document {NS}><w:body>{body}</w:body></w:document>"
            )
    return out.getvalue()


NS = (
    'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main" '
    'xmlns:mc="http://schemas.openxmlformats.org/markup-compatibility/2006"'
)


def test_plain_characters_are_those_word_shows() -> None:
    plain_chars = _script().plain_chars
    run = "<w:r><w:t>{}</w:t></w:r>"
    # An insertion counts; a deletion's w:delText does not.
    tracked = '<w:p><w:ins w:id="1">' + run.format("abc") + '</w:ins><w:del w:id="2">'
    tracked += "<w:r><w:delText>xyz</w:delText></w:r></w:del></w:p>"
    assert plain_chars(_docx(tracked)) == 3
    # A field's result counts; its code does not.
    field = '<w:p><w:r><w:fldChar w:fldCharType="begin"/></w:r>'
    field += "<w:r><w:instrText> PAGE </w:instrText></w:r>"
    field += '<w:r><w:fldChar w:fldCharType="separate"/></w:r>' + run.format("12")
    field += '<w:r><w:fldChar w:fldCharType="end"/></w:r></w:p>'
    assert plain_chars(_docx(field)) == 2
    # Of markup compatibility's alternatives, the choice Word draws, not the fallback.
    choice = '<w:p><mc:AlternateContent><mc:Choice Requires="w14">' + run.format("abcdef")
    choice += "</mc:Choice><mc:Fallback>" + run.format("abc") + "</mc:Fallback>"
    choice += "</mc:AlternateContent></w:p>"
    assert plain_chars(_docx(choice)) == 6
    # No document part, or no package: nothing to count, not 0.
    assert plain_chars(_docx(None)) is None
    assert plain_chars(b"not a zip") is None


def test_a_small_hold_out_group_shows_no_aggregates() -> None:
    script = _script()
    assert script._cell(RECORDS[:3]) == {"files": "<5"}
    assert script._cell(RECORDS)["files"] == 6


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
        _section("a", "2", ["formatting: strike"]),
        # b is whole now: better, so not listed.
        _file("b", "whole", 100, 100, carried=2, sections=2),
        _section("b", "1"),
        _section("b", "2"),
        _file("c", "built-section-refused", 100, 50, carried=1, sections=2),
        _section("c", "1"),
        _section("c", "2", ["tab: a tab"]),
        _file("d", "reader-refused", 200, code="tracked-change"),
        _file("e", "reader-refused", 100, code="tab"),
        _file("f", "reader-refused", None, code="invalid-package"),
    ]
    found = script.regress(before, _board(tmp_path / "after", after))
    assert found["versions"] == {"before": {"builder": "before"}, "after": {"builder": "after"}}
    assert [(f["sha256"], f["before"], f["after"]) for f in found["worseFiles"]] == [
        ("a", "whole", "built-section-refused"),
        ("e", "needs-a-person", "reader-refused"),
    ]
    assert [(s["sha256"], s["section"], s["change"]) for s in found["sections"]] == [
        ("a", "1", "changed"),
        ("a", "2", "refused: formatting"),
    ]
    # 1 says only that something regressed; unreadable input is 2.
    assert script.main(["regress", str(before), str(before)]) == 0
    assert script.main(["regress", str(before), str(tmp_path / "after")]) == 1
    assert script.main(["regress", str(before), str(tmp_path)]) == 2
    (tmp_path / "after" / "sections.jsonl").write_text("{not json\n", encoding="utf-8")
    assert script.main(["regress", str(before), str(tmp_path / "after")]) == 2
