"""Where each label stands on a CCDS change, from its own text (``zone_a.implementation``).

Every document here is synthetic, built in the test; the one pinned ePI is read only for its
statuses, and no assertion shows its text.
"""

from __future__ import annotations

import datetime
import importlib.util
import io
import itertools
import json
import random
import zipfile
from collections.abc import Callable
from pathlib import Path

import pytest
from label_docx.reader import Mark, Paragraph

from zone_a import implementation as impl
from zone_a.canonical_json import canonical_json

ROOT = Path(__file__).resolve().parents[2]
W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"


def _docx(*paragraphs: str) -> bytes:
    """A minimal .docx whose body is these paragraphs (a "#" before the text: a heading)."""
    body = ""
    for text in paragraphs:
        style = '<w:pPr><w:pStyle w:val="Heading2"/></w:pPr>' if text.startswith("#") else ""
        body += f'<w:p>{style}<w:r><w:t xml:space="preserve">{text.lstrip("#")}</w:t></w:r></w:p>'
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as package:
        package.writestr(
            "[Content_Types].xml",
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '<Default Extension="xml" ContentType="application/xml"/></Types>',
        )
        package.writestr(
            "_rels/.rels",
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="r1" Type="http://schemas.openxmlformats.org/officeDocument/2006/'
            'relationships/officeDocument" Target="word/document.xml"/></Relationships>',
        )
        package.writestr(
            "word/_rels/document.xml.rels",
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"/>',
        )
        package.writestr(
            "word/document.xml", f'<w:document xmlns:w="{W}"><w:body>{body}</w:body></w:document>'
        )
    return out.getvalue()


def _p(text: str, style: str | None = None, marks: tuple[Mark, ...] = ()) -> Paragraph:
    return Paragraph(text, style, None, None, marks)


OLD_TB = "Serious infections, including tuberculosis, have been reported."
NEW_TB = (
    "Serious infections, including tuberculosis and hepatitis B reactivation, have been reported."
)
LOCAL_TB = (
    "Serious infections, including tuberculosis and reactivation of hepatitis B, have been "
    "reported."
)


def _hbv() -> impl.Edit:
    old, new = [_p("Warnings", "Heading2"), _p(OLD_TB)], [_p("Warnings", "Heading2"), _p(NEW_TB)]
    (edit,) = impl.edits(old, new, impl.changes(old, new))
    return edit


def test_segments_give_back_both_texts_exactly() -> None:
    rng = random.Random(20261005)
    pieces = ["dose", "5", "mg", "≤", "≥", "µg", " ", "\u00a0", ",", ".", "Take", "with", "food"]
    for _ in range(2000):
        old = "".join(rng.choice(pieces) for _ in range(rng.randint(0, 12)))
        new = "".join(rng.choice(pieces) for _ in range(rng.randint(0, 12)))
        found = impl.segments(old, new)
        kinds = [s.kind for s in found]
        assert "".join(s.text for s in found if s.kind != "i") == old
        assert "".join(s.text for s in found if s.kind != "d") == new
        assert all(a != b for a, b in itertools.pairwise(kinds))


def test_a_symbol_is_a_token_of_its_own() -> None:
    assert impl.segments("dose ≤ 5 mg", "dose ≥ 5 mg") == (
        impl.Segment("t", "dose "),
        impl.Segment("d", "≤"),
        impl.Segment("i", "≥"),
        impl.Segment("t", " 5 mg"),
    )


def test_paragraphs_are_paired_and_formatting_is_reported() -> None:
    bold = (Mark(0, 4, "bold"),)
    old = [
        _p("Posology", "Heading2"),
        _p("Take 5 mg daily."),
        _p("Store cold."),
        _p(""),
        _p("Gone."),
    ]
    new = [
        _p("Posology", "Heading2"),
        _p("Take 10 mg daily."),
        _p("Store cold.", marks=bold),
        _p("New sentence."),
    ]
    found = impl.changes(old, new)
    assert [(c.kind, c.old_index, c.new_index, c.heading) for c in found] == [
        ("text", 1, 1, "Posology"),
        ("formatting", 2, 2, "Posology"),
        ("text", 4, 3, "Posology"),
    ]


def test_inserted_and_deleted_paragraphs_stand_alone() -> None:
    old = [_p("Keep."), _p("Same one."), _p("Removed whole.")]
    new = [_p("Keep."), _p("Added whole."), _p("Same one.")]
    found = impl.changes(old, new)
    assert [(c.kind, c.old_index, c.new_index) for c in found] == [
        ("inserted", None, 1),
        ("deleted", 2, None),
    ]
    assert [(e.old, e.new) for e in impl.edits(old, new, found)] == [
        (None, "Added whole."),
        ("Removed whole.", None),
    ]


def test_an_edit_keeps_four_words_of_context_on_each_side() -> None:
    old = [_p("One two three four five six seven alpha eight nine ten eleven twelve.")]
    new = [_p("One two three four five six seven beta eight nine ten eleven twelve.")]
    (edit,) = impl.edits(old, new, impl.changes(old, new))
    assert (edit.old, edit.new, edit.reason) == (
        "four five six seven alpha eight nine ten eleven",
        "four five six seven beta eight nine ten eleven",
        None,
    )


def test_close_changes_are_one_edit_and_far_ones_two() -> None:
    near = impl.segments("a b c d", "a X c Y")
    assert [s.kind for s in near] == ["t", "d", "i", "t", "d", "i"]
    words = " ".join(f"w{n}" for n in range(30))
    old = [_p(f"start {words} end")]
    new = [_p(f"begin {words} finish")]
    assert len(impl.edits(old, new, impl.changes(old, new))) == 2
    old = [_p("x a b c d y")]
    new = [_p("X a b c d Y")]
    assert len(impl.edits(old, new, impl.changes(old, new))) == 1


def test_context_grows_until_the_wording_is_unique_or_cannot_be() -> None:
    # "5 mg" with four words around it occurs twice; with five, once.
    shared = "the usual dose is 5 mg daily for adults"
    old = [_p(f"First {shared} only."), _p(f"Second {shared} only.")]
    new = [_p(f"First {shared.replace('5 mg', '10 mg')} only."), _p(f"Second {shared} only.")]
    (edit,) = impl.edits(old, new, impl.changes(old, new))
    assert edit.reason is None
    assert edit.old is not None
    assert edit.old.startswith("First")
    # The whole changed paragraph also stands inside another: no context tells them apart.
    old = [_p("Take with food."), _p("Note: Take with food. Always.")]
    new = [_p("Take with water."), _p("Note: Take with food. Always.")]
    (edit,) = impl.edits(old, new, impl.changes(old, new))
    assert edit.reason == "ambiguous-wording"


def _label(*texts: str, language: str = "en", refused_part: bool = False) -> impl.Label:
    return impl.Label("label", language, tuple(_p(t) for t in texts), refused_part=refused_part)


def test_a_label_is_judged_by_its_text() -> None:
    edit = _hbv()
    assert impl.check(edit, _label("Intro.", NEW_TB), "en").status == "implemented"
    assert impl.check(edit, _label("Intro.", NEW_TB), "en").new == ((1, 0),)
    assert impl.check(edit, _label(OLD_TB), "en").status == "pending"
    assert impl.check(edit, _label(OLD_TB, NEW_TB), "en").status == "both"
    absent = impl.check(edit, _label(LOCAL_TB), "en")
    assert (absent.status, absent.spacing) == ("absent", False)


def test_spaces_that_differ_are_noted_and_never_matched() -> None:
    edit = _hbv()
    spaced = impl.check(edit, _label(NEW_TB.replace("hepatitis B", "hepatitis\u00a0B")), "en")
    assert (spaced.status, spaced.spacing) == ("absent", True)


def test_what_cannot_be_checked_says_why() -> None:
    edit = _hbv()
    assert impl.check(edit, _label(NEW_TB, language="de"), "en").reason == "language"
    refused = impl.Label("label", "en", refusal="tracked-change")
    assert impl.check(edit, refused, "en").reason == "refused"
    part = impl.check(edit, _label(NEW_TB, refused_part=True), "en")
    assert (part.status, part.reason, part.new) == ("not-checked", "refused-part", ((0, 0),))
    ambiguous = impl.Edit("x", 0, "a", "b", "ambiguous-wording")
    assert impl.check(ambiguous, _label("a"), "en").reason == "ambiguous-wording"


def test_one_wording_inside_the_other_belongs_to_the_longer() -> None:
    old = [_p("Store below 25 °C")]
    new = [_p("Store below 25 °C in the outer carton")]
    (edit,) = impl.edits(old, new, impl.changes(old, new))
    assert edit.new is not None
    assert edit.old is not None
    assert edit.old in edit.new
    assert impl.check(edit, _label("Store below 25 °C in the outer carton"), "en").status == (
        "implemented"
    )
    assert impl.check(edit, _label("Store below 25 °C"), "en").status == "pending"


def test_a_deleted_paragraph_is_never_called_implemented() -> None:
    old, new = [_p("Keep."), _p("Removed whole.")], [_p("Keep.")]
    (edit,) = impl.edits(old, new, impl.changes(old, new))
    assert impl.check(edit, _label("Keep.", "Removed whole."), "en").status == "pending"
    assert impl.check(edit, _label("Keep."), "en").status == "absent"


def test_the_report_from_word_files_end_to_end(tmp_path: Path) -> None:
    spec = importlib.util.spec_from_file_location(
        "check_implementation", ROOT / "zone-a" / "scripts" / "check_implementation.py"
    )
    assert spec is not None
    assert spec.loader is not None
    check_implementation = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(check_implementation)
    old = _docx("#Special warnings", OLD_TB, "Store below 25 °C.")
    new = _docx("#Special warnings", NEW_TB, "Store below 25 °C.")
    (tmp_path / "old.docx").write_bytes(old)
    (tmp_path / "new.docx").write_bytes(new)
    (tmp_path / "uk.docx").write_bytes(_docx("#Special warnings", NEW_TB))
    (tmp_path / "ie.docx").write_bytes(_docx("#Special warnings", OLD_TB))
    (tmp_path / "de.docx").write_bytes(_docx("Schwerwiegende Infektionen."))
    (tmp_path / "broken.docx").write_bytes(b"not a zip")
    entries = [
        {"file": "uk.docx", "language": "en"},
        {"file": "ie.docx", "language": "en"},
        {"file": "de.docx", "language": "de"},
        {"file": "broken.docx", "language": "en"},
    ]
    (tmp_path / "labels.json").write_text(json.dumps(entries), encoding="utf-8")
    out = tmp_path / "report.json"
    paths = [str(tmp_path / name) for name in ("old.docx", "new.docx", "labels.json")]
    assert check_implementation.main([*paths, "--out", str(out)]) == 0
    result = json.loads(out.read_text(encoding="utf-8"))
    assert result["checker"] == impl.IMPLEMENTATION_VERSION
    assert [c["heading"] for c in result["changes"]] == ["Special warnings"]
    statuses = [
        (entry["name"], [r["status"] for r in entry["results"]]) for entry in result["labels"]
    ]
    assert statuses == [
        ("uk.docx", ["implemented"]),
        ("ie.docx", ["pending"]),
        ("de.docx", ["not-checked"]),
        ("broken.docx", ["not-checked"]),
    ]
    assert result["labels"][3]["refusal"] == "invalid-package"
    assert canonical_json(result) + "\n" == out.read_text(encoding="utf-8")


def test_an_epi_is_read_section_by_section() -> None:
    source = sorted((ROOT / "labels" / "ema-epi" / "sources").glob("*.json"))[0]
    label = impl.read_label(source.name, "en", source.read_bytes())
    assert (label.refusal, len(label.paragraphs) > 50) == (None, True)
    edit = _hbv()
    assert impl.check(edit, label, "en").status in ("absent", "not-checked")


@pytest.mark.parametrize("bad", ["", "   "])
def test_a_blank_paragraph_is_layout(bad: str) -> None:
    assert impl.changes([_p("A.")], [_p("A."), _p(bad)]) == []


DE_OLD = "Schwerwiegende Infektionen, einschließlich Tuberkulose, wurden berichtet."
DE_NEW = (
    "Schwerwiegende Infektionen, einschließlich Tuberkulose und Hepatitis-B-Reaktivierung, "
    "wurden berichtet."
)


def test_a_label_in_another_language_is_checked_against_its_given_wording() -> None:
    edit = _hbv()
    given: impl.Wordings = {edit.id: {"de": (DE_OLD, DE_NEW)}}
    implemented = impl.check(edit, _label(DE_NEW, language="de"), "en", given)
    assert (implemented.status, implemented.wording) == ("implemented", "translation")
    assert impl.check(edit, _label(DE_OLD, language="de"), "en", given).status == "pending"
    assert impl.check(edit, _label(DE_NEW, language="fr"), "en", given).reason == "language"
    assert impl.check(edit, _label(NEW_TB), "en", given).wording == "ccds"


def _file(edit: impl.Edit, **languages: object) -> dict[str, object]:
    return {edit.id: {"ccds": {"language": "en", "old": edit.old, "new": edit.new}, **languages}}


@pytest.mark.parametrize(
    ("change", "message"),
    [
        (lambda _e: {"0" * 12: {}}, "no edit"),
        (lambda e: {e.id: {"ccds": {"old": "other", "new": e.new}}}, "other CCDS wording"),
        (lambda e: _file(e, en={"old": "a", "new": "b"}), "own language"),
        (lambda e: _file(e, de={"new": DE_NEW}), "old and new"),
        (lambda e: _file(e, de={"old": DE_OLD, "new": None}), "new must be given"),
        (lambda e: _file(e, de={"old": " ", "new": DE_NEW}), "old is empty"),
        (lambda e: _file(e, de={"old": DE_NEW, "new": DE_NEW}), "the same"),
        (lambda e: [e.id], "JSON object"),
    ],
)
def test_a_wording_file_is_refused_whole_when_anything_is_wrong(
    change: Callable[[impl.Edit], object], message: str
) -> None:
    edit = _hbv()
    with pytest.raises(ValueError, match=message):
        impl.load_wordings(change(edit), [edit], "en")


def test_an_inserted_paragraph_takes_no_old_wording_in_any_language() -> None:
    old, new = [_p("Keep.")], [_p("Keep."), _p("Added whole.")]
    (edit,) = impl.edits(old, new, impl.changes(old, new))
    with pytest.raises(ValueError, match="old must be null"):
        impl.load_wordings(_file(edit, de={"old": "Alt.", "new": "Neu."}), [edit], "en")
    assert impl.load_wordings(_file(edit, de={"old": None, "new": "Neu."}), [edit], "en") == {
        edit.id: {"de": (None, "Neu.")}
    }


def test_a_template_lists_each_checkable_edit_to_fill_in() -> None:
    edit = _hbv()
    ambiguous = impl.Edit("x" * 12, 0, "a", "b", "ambiguous-wording")
    made = impl.template([edit, ambiguous], "en", ["de", "fr"])
    assert list(made) == [edit.id]
    assert made[edit.id]["de"] == {"old": None, "new": None}
    # Left empty, a language is not given; filled in, it is.
    assert impl.load_wordings(made, [edit], "en") == {}
    made[edit.id]["de"] = {"old": DE_OLD, "new": DE_NEW}
    assert impl.load_wordings(made, [edit], "en") == {edit.id: {"de": (DE_OLD, DE_NEW)}}


def test_lateness_counts_from_the_date_given_never_a_clock() -> None:
    edit = _hbv()
    due = datetime.date(2026, 9, 1)
    on = datetime.date(2026, 10, 5)
    pending = impl.Label("ie", "en", (_p(OLD_TB),), market="IE", due=due)
    done = impl.Label("uk", "en", (_p(NEW_TB),), market="UK", due=due)
    other = impl.Label("de", "de", (_p(DE_NEW),), market="DE", due=due)
    undated = impl.Label("mt", "en", (_p(OLD_TB),), market="MT")
    assert impl.days_late(impl.check(edit, pending, "en"), pending, on) == 34
    assert impl.days_late(impl.check(edit, pending, "en"), pending, due) == 0
    # Before its date a label is not late, never early by a negative count.
    early = due - datetime.timedelta(days=10)
    assert impl.days_late(impl.check(edit, pending, "en"), pending, early) == 0
    assert impl.days_late(impl.check(edit, done, "en"), done, on) == 0
    assert impl.days_late(impl.check(edit, other, "en"), other, on) is None
    assert impl.days_late(impl.check(edit, undated, "en"), undated, on) is None


def test_the_report_counts_each_edit_and_names_the_late_markets() -> None:
    old, new = [_p("Warnings", "Heading2"), _p(OLD_TB)], [_p("Warnings", "Heading2"), _p(NEW_TB)]
    due = datetime.date(2026, 9, 1)
    labels = [
        impl.Label("uk", "en", (_p(NEW_TB),), market="UK", due=due),
        impl.Label("ie", "en", (_p(OLD_TB),), market="IE", due=due),
        impl.Label("ie", "en", (_p(OLD_TB),), market="IE", due=due),
        impl.Label("de", "de", (_p(DE_NEW),), market="DE", due=due),
    ]
    sources = {"old": "a", "new": "b"}
    with pytest.raises(ValueError, match="as of"):
        impl.report(old, new, "en", labels, sources)
    result = impl.report(old, new, "en", labels, sources, as_of=datetime.date(2026, 10, 5))
    ((edit_id, summary),) = result["summary"].items()
    assert summary == {
        "implemented": 1,
        "pending": 2,
        "both": 0,
        "absent": 0,
        "not-checked": 1,
        "late": 2,
        "lateMarkets": ["IE"],
    }
    # Two labels may share a name: each keeps its own result.
    assert [entry["results"][0]["daysLate"] for entry in result["labels"]] == [0, 34, 34, None]
    assert result["asOf"] == "2026-10-05"
    assert edit_id == result["edits"][0]["id"]


def test_the_script_writes_a_template_and_a_dated_translated_report(tmp_path: Path) -> None:
    spec = importlib.util.spec_from_file_location(
        "check_implementation", ROOT / "zone-a" / "scripts" / "check_implementation.py"
    )
    assert spec is not None
    assert spec.loader is not None
    script = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(script)
    (tmp_path / "old.docx").write_bytes(_docx(OLD_TB))
    (tmp_path / "new.docx").write_bytes(_docx(NEW_TB))
    (tmp_path / "de.docx").write_bytes(_docx(DE_NEW))
    ccds = [str(tmp_path / "old.docx"), str(tmp_path / "new.docx")]
    made = tmp_path / "wordings.json"
    assert script.main([*ccds, "--template", "de", "--out", str(made)]) == 0
    filled = json.loads(made.read_text(encoding="utf-8"))
    (edit_id,) = filled
    filled[edit_id]["de"] = {"old": DE_OLD, "new": DE_NEW}
    made.write_text(json.dumps(filled), encoding="utf-8")
    entries = [{"file": "de.docx", "language": "de", "market": "DE", "due": "2026-09-01"}]
    (tmp_path / "labels.json").write_text(json.dumps(entries), encoding="utf-8")
    labels = str(tmp_path / "labels.json")
    with pytest.raises(SystemExit):  # a date, and no day to count lateness on
        script.main([*ccds, labels, "--wordings", str(made)])
    out = tmp_path / "report.json"
    dated = [*ccds, labels, "--wordings", str(made), "--as-of", "2026-10-05", "--out", str(out)]
    assert script.main(dated) == 0
    (result,) = json.loads(out.read_text(encoding="utf-8"))["labels"][0]["results"]
    assert (result["status"], result["wording"], result["daysLate"]) == (
        "implemented",
        "translation",
        0,
    )
