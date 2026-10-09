"""The survey's cause list: every reason a document is refused, a measurement only.

``scripts/survey.py --causes`` reads a copy of each refused document again with each refused
construct taken out, so a document blocked by more than one thing shows each. The reader itself
is not changed: its result still refuses at the first.
"""

from __future__ import annotations

import json
import xml.etree.ElementTree as ET

import survey
from label_docx import read
from test_reader import docx, p, r
from test_tracked import BEGIN, END, SEPARATE, code, ins, t

WINGDINGS = r("<w:t>x</w:t>", '<w:rFonts w:ascii="Wingdings" w:hAnsi="Wingdings"/>')
OBJECT = r("<w:object/>")


def test_every_cause_is_listed_once_in_the_order_met_and_the_read_still_refuses_at_the_first() -> (
    None
):
    data = docx(p(t("a")) + p(WINGDINGS) + p(OBJECT) + p(WINGDINGS))
    found = survey.causes(data)
    assert found == (
        ["symbol-font: a run in a dingbat or symbol-encoded font", "unsupported-element: object"],
        "read",
    )
    assert survey.causes(data) == found  # the same every time
    refusal = json.loads(read(data)[0])["refusal"]
    assert survey.cause(refusal["code"], refusal["detail"]) == found[0][0]
    assert survey.causes(docx(p(t("a")))) == ([], "read")


def test_the_views_of_a_tracked_document_are_surveyed_each_as_a_document() -> None:
    # A field the original keeps in part (in the document), and a Wingdings run (in a view).
    field = BEGIN + code(" HYPERLINK x ") + SEPARATE + t("site") + ins(END)
    found, end = survey.causes(docx(p(field) + p(t("a") + ins(t("b"))) + p(WINGDINGS)))
    assert found == [
        "tracked-change: a change holds part of a field",
        "symbol-font: a run in a dingbat or symbol-encoded font",
    ]
    assert end == "views"


def test_a_cause_no_element_of_which_is_in_the_copy_ends_the_search() -> None:
    assert survey.causes(b"not a package") == (
        ["invalid-package: not a zip archive from its first byte"],
        "unplaced",
    )


def test_a_cause_shows_no_number_view_or_quoted_value() -> None:
    assert (
        survey.cause("stale-field", "accepted view: a SEQ field shows '12'; Word prints \"a b\"")
        == "stale-field: a SEQ field shows '…'; Word prints \"…\""
    )


def test_the_ranking_lifts_first_the_cause_that_reads_the_most_documents() -> None:
    found = {"a": ["x"], "b": ["x"], "c": ["y", "z"], "d": ["z"], "e": ["y"], "f": []}
    assert survey.rank(found) == [("x", 2, 2), ("y", 1, 3), ("z", 2, 5)]


def test_a_paragraph_taken_out_keeps_its_bookmarks_and_is_taken_out_once() -> None:
    marked = p('<w:bookmarkStart w:id="1" w:name="B"/>' + WINGDINGS + '<w:bookmarkEnd w:id="1"/>')
    scratch = survey._Scratch(docx(marked + p(t("a"))))
    root = scratch.parts["word/document.xml"]
    (held,) = [e for e in root.iter(f"{{{survey.W}}}p") if len(e) == 3]
    suspect = ET.tostring(held)
    assert scratch.take_out(suspect)
    assert [e.tag.rsplit("}", 1)[1] for e in held] == ["bookmarkStart", "bookmarkEnd"]
    assert not scratch.take_out(ET.tostring(held))  # nothing more to take out
    assert survey.causes(scratch.data()) == ([], "read")
