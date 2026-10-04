"""Mutation testing of the conservation check: does every fault in it make a test fail?

    uv run --frozen python scripts/mutate_checker.py          # run, print the survivors
    uv run --frozen python scripts/mutate_checker.py --write  # and write the record
    uv run --frozen python scripts/mutate_checker.py --write --budget 100  # in parts

A run takes about half an hour on eight cores. It runs on a copy of the package, tests,
scripts, corpus and ``pyproject.toml`` made when it starts, so an edit during the run changes
nothing in it; the record is written only if the files are still as copied at the end. Each
mutant's result is kept as it comes in (``.mutants-progress.json``) with a fingerprint of that
copy. With ``--budget MINUTES`` a run starts no mutant after that time, keeps what it has and
stops (exit 3); the next run takes up where it stopped. If anything in the fingerprint changed,
every mutant is run again.

The check (``src/label_docx/certify.py``) is what the proof rests on, so its tests must hold
it, not merely run it. This script makes one small fault at a time in a copy of it: a comparison
turned round (``==`` to ``!=``, ``<`` to ``<=``, ``in`` to ``not in``), ``and`` to ``or``, a
``not`` dropped, a number one off, a ``True`` made ``False``, one character of a string made
another, an addition made a subtraction, a ``raise``, ``return``, ``continue`` or ``break``
removed or a refusal made to pass, a call's result discarded. Each faulty copy is run against
the check's tests (``TESTS``); a fault the tests notice is killed, and the test that last killed
it is tried first next time. A fault that survives is either
a missing test, to be added, or a change that cannot alter what the check does (an equivalent
mutant), to be recorded in ``EQUIVALENT`` below with the reason. A mutant is named by its
function, its line's code, its kind and which of the mutants so named it is, in source order
(0 for the first), so a reason excuses one mutant only. ``tests/test_checker_mutants.py`` holds
the recorded run: every mutant killed or recorded as equivalent, of this ``certify.py``, these
tests and this corpus.
"""

from __future__ import annotations

import argparse
import ast
import concurrent.futures
import copy
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / "src" / "label_docx" / "certify.py"
RECORD = ROOT / "docs" / "checker-mutants.json"
PROGRESS = ROOT / ".mutants-progress.json"
# For each mutant, by function, code and kind, the test that last killed it: tried first.
KILLERS = ROOT / ".mutants-killers.json"
# Exit status of a run stopped by its budget, with mutants left to run.
UNFINISHED = 3
TESTS = [
    "tests/test_certify.py",
    "tests/test_headers_comments.py",
    "tests/test_tracked.py",
    "tests/test_generated_docx.py",
    "tests/test_robustness.py",
    "tests/test_output.py",
]

_SYMBOL_RANGE = "low = code - 0xF000 if 0xF000 <= code <= 0xF0FF else code"
_TABLE_EDGE = (
    "the Symbol table holds neither 0x00 nor 0xFF nor 0x100: a code at either edge of the "
    "U+F000 range, or just past it, is refused before and after"
)
_XML_PREFIX = "the xml prefix can never name Word's namespace, the only one the tokenizer looks for"
_FIRST_LESS = "a '<' at the very start finds no capture open, so the chunk is never used there"
_STRICT = "the lengths are compared just before, so zip(strict=True) never raises"
_LEDGER = (
    "the counts follow from the sequences, which are equal by then: the ledger is a second "
    "statement of the same fact, stated in the certificate"
)
_ROW_GROUP = (
    "a row group is always met beside a table or a row, which end the paragraph; text directly "
    "in one is refused by the reader"
)
_SECTIONS_ZIP = (
    'for index, (mine, theirs) in enumerate(zip(self.sections, value["sections"], strict=True)):'
)
_SYMBOL_COUNT = "self.ledger.symbol += sum(1 for a, b in zip(text, mapped, strict=True) if a != b)"
# Mutants that cannot change what the check does, by function, line, mutation and which of the
# mutants so named (0 for the first in the source), each with the reason. A survivor not listed
# here fails the run.
_TABLE_STEP = (
    "the table count is compared only between two walks that both make it: any step but zero "
    "numbers the same tables apart, so a table added or lost shows either way"
)
_WALK_AGAIN = (
    "a paragraph holds no paragraph and run content no run the walk reads: walking either"
    " again only adds to a list outside any paragraph, which is never read"
)
_PLACE = "the words only name the place in a refusal's detail"
_MATCH_PLACE = '_match(mine, paragraph, f"{where} {index + 1}")'
_SECTION_PLACE = 'section(mine, theirs, f"section {index + 1}")'
_SUBSECTION_PLACE = 'section(child, other, f"{where}.{index + 1}")'
_NOTE_PLACE = 'part, theirs_by_id[note_id]["paragraphs"], f"{kind} {note_id} paragraph"'
_ATTRIBUTES = (
    "outside a namespace declaration an attribute is read only for its references, which the "
    "XML parser has checked first (_parse)"
)
_GUARD = '_ATTRIBUTE.findall(body, len(name)) if "xmlns" in body or "&" in body else ()'
EQUIVALENT: dict[tuple[str, str, str, int], str] = {
    ("_raw_texts", _GUARD, "compare:0", 1): _ATTRIBUTES,
    ("_raw_texts", _GUARD, "str:'&'", 0): _ATTRIBUTES,
    (
        "_refused",
        '_paragraphs(part, theirs["paragraphs"], f"{where} paragraph")',
        "str:' paragraph'",
        0,
    ): (_PLACE),
    ("_paragraphs", _MATCH_PLACE, "str:' '", 0): _PLACE,
    ("_paragraphs", _MATCH_PLACE, "binop", 0): _PLACE,
    ("_paragraphs", _MATCH_PLACE, "int:1", 0): _PLACE,
    ("EpiSource.certify", _SECTION_PLACE, "str:'section '", 0): _PLACE,
    ("EpiSource.certify", _SECTION_PLACE, "binop", 0): _PLACE,
    ("EpiSource.certify", _SECTION_PLACE, "int:1", 0): _PLACE,
    ("EpiSource.certify.section", _SUBSECTION_PLACE, "str:'.'", 0): _PLACE,
    ("EpiSource.certify.section", _SUBSECTION_PLACE, "binop", 0): _PLACE,
    ("EpiSource.certify.section", _SUBSECTION_PLACE, "int:1", 0): _PLACE,
    ("DocxSource.certify", _NOTE_PLACE, "str:' '", 0): _PLACE,
    ("DocxSource.certify", _NOTE_PLACE, "str:' paragraph'", 0): _PLACE,
    (
        "DocxSource._pairs",
        "if f\"{self.comments_part}#{comment['id']}\" not in refused:",
        "str:'#'",
        0,
    ): (
        "a refused comment is served with no paragraphs (certify holds it to that): counting "
        "it adds nothing to the output"
    ),
    # After a paragraph, and after run content.
    ("_run_tokens.walk", "continue", "statement", 0): _WALK_AGAIN,
    (
        "_run_tokens.walk",
        'gone = dropped or (child.tag in dropping and element.tag != _w("rPr"))',
        "str:'rPr'",
        0,
    ): "a paragraph mark's change markers are empty: dropping one drops no content",
    ("_run_tokens.table", "index, tables = tables, tables + 1", "binop", 0): _TABLE_STEP,
    ("_run_tokens.table", "index, tables = tables, tables + 1", "int:1", 0): _TABLE_STEP,
    ("_raw_texts", "found = _TAG.match(text, less + 1)", "int:1", 0): (
        "a tag's first character is its name's, never '>' or a quote, so matching from one "
        "character later finds the same end"
    ),
    ("_local", 'return tag.rsplit("}", 1)[-1]', "int:1", 0): (
        "an element's tag holds one '}', after its namespace"
    ),
    (
        "_on",
        'return element.get(_w("val"), "true").lower() not in ("0", "false", "off")',
        "str:'true'",
        0,
    ): "any value outside the off list is on: 'Xrue' is on as 'true' is",
    ("_Fonts.font", "return None", "statement", 0): "the function returns None at its end anyway",
    ("_symbol_reading", _SYMBOL_RANGE, "compare:0", 0): _TABLE_EDGE,
    ("_symbol_reading", _SYMBOL_RANGE, "compare:1", 0): _TABLE_EDGE,
    # "0xF000 <= code", not "code - 0xF000".
    ("_symbol_reading", _SYMBOL_RANGE, "int:61440", 1): _TABLE_EDGE,
    ("_symbol_reading", _SYMBOL_RANGE, "int:61695", 0): _TABLE_EDGE,
    ("_Story.token", _SYMBOL_RANGE, "compare:0", 0): _TABLE_EDGE,
    ("_Story.token", _SYMBOL_RANGE, "compare:1", 0): _TABLE_EDGE,
    ("_Story.token", _SYMBOL_RANGE, "int:61440", 1): _TABLE_EDGE,
    ("_Story.token", _SYMBOL_RANGE, "int:61695", 0): _TABLE_EDGE,
    # The last variation selector, U+E01EF: one past it, U+E01F0, is unassigned (Cn), which the
    # check refuses as such, so a range that ends one later refuses nothing more.
    ("<module>", "(0xE0100, 0xE01EF),", "int:917999", 0): (
        "U+E01F0, one past the range, is unassigned and refused as such before and after"
    ),
    ("_Story.token", _SYMBOL_COUNT, "bool", 0): (
        "the Symbol table maps one character to one, so the two are the same length"
    ),
    ("_Story.flush_run", "return", "statement", 0): (
        "an empty run then adds an empty segment, or no hidden characters: nothing either way"
    ),
    (
        "DocxSource.__init__",
        'if note.get(_w("type"), "normal") in _NOTE_LAYOUT:',
        "str:'normal'",
        0,
    ): ("the default only has to be outside the layout types, as 'Xormal' is"),
    (
        "DocxSource.certify",
        'raise CertificationError("the ledger does not balance")',
        "statement",
        0,
    ): (_LEDGER),
    ("<module>", '"thead",', "str:'thead'", 0): _ROW_GROUP,
    ("<module>", '"tbody",', "str:'tbody'", 0): _ROW_GROUP,
    ("_Div.__init__", "self.pending = False", "bool", 0): (
        "the parser starts at the div, a block, which ends the (empty) paragraph and clears it"
    ),
    # The cell count's -1, not the row count's.
    ("_Div.handle_starttag", "self.open_tables.append([self.tables, -1, -1])", "int:1", 1): (
        "the cell count starts again at each row; only the row count's start matters, and its "
        "mutant is caught"
    ),
    ("EpiSource._section", "parsed.close()", "call", 0): (
        "the div ends with its own end tag, so nothing is left buffered"
    ),
    ("EpiSource._section", "parsed.flush()", "call", 0): (
        "the div's end tag, a block's, has ended the last paragraph"
    ),
    ("EpiSource.certify.section", "if a != b", "compare:0", 0): (
        "where a difference is found only words the refusal"
    ),
    (
        "EpiSource.certify.section",
        'zip(mine.sections, theirs["sections"], strict=True)',
        "bool",
        0,
    ): (_STRICT),
    ("EpiSource.certify", _SECTIONS_ZIP, "bool", 0): _STRICT,
    ("_refused", "return False", "statement", 0): (
        "a function that ends without a return gives None, which is as false as False here"
    ),
    (
        "DocxSource.certify",
        '_paragraphs(self.body, value["paragraphs"], "paragraph")',
        "str:'paragraph'",
        0,
    ): "the word only names the place in a refusal's detail",
    (
        "DocxSource.certify",
        'for (comment, part), theirs in zip(self.comments, value["comments"], strict=True):',
        "bool",
        0,
    ): "the comments' metadata lists are compared just before, so the lengths are equal",
    (
        "DocxSource.certify",
        "for (name, _, part), story in zip(found, theirs, strict=True):",
        "bool",
        0,
    ): "the parts and their uses are compared just before, so the lengths are equal",
    (
        "DocxSource._pairs",
        'for (comment, part), theirs in zip(self.comments, value["comments"], strict=True):',
        "bool",
        0,
    ): "called only after certify has compared the lengths",
    (
        "DocxSource._pairs",
        'for (name, _, part), story in zip(found, value[kind + "s"], strict=True):',
        "bool",
        0,
    ): "called only after certify has compared the lengths",
    ("_Fonts.marks.nearest", "return None", "statement", 0): (
        "the function returns None at its end anyway"
    ),
    (
        "_match",
        "at = next(i for i, (a, b) in enumerate(zip(shown, claimed, strict=True)) if set(a) != b)",
        "bool",
        0,
    ): "both lists have one entry per character of the text, which was compared just before",
    (
        "_raw_texts",
        'scopes: list[dict[str, str]] = [{"xml": _XML_NS}]',
        "str:'xml'",
        0,
    ): _XML_PREFIX,
    ("_raw_texts", "depth_of_capture = -1", "int:1", 0): (
        "the depth is set whenever a capture begins, before it is read"
    ),
    ("_raw_texts", "chunk = text[at:] if less < 0 else text[at:less]", "compare:0", 0): _FIRST_LESS,
    ("_raw_texts", "chunk = text[at:] if less < 0 else text[at:less]", "int:0", 0): _FIRST_LESS,
    ("_raw_texts", 'name = body.split(None, 1)[0] if body.strip() else ""', "int:1", 0): (
        "splitting once more leaves the first word, the name, the same"
    ),
    (
        "_paragraphs",
        "for index, (mine, paragraph) in enumerate(zip(part.paragraphs, theirs, strict=True)):",
        "bool",
        0,
    ): _STRICT,
    (
        "EpiSource.certify",
        'raise CertificationError("the ledger does not balance")',
        "statement",
        0,
    ): (_LEDGER),
    ("<module>", "custom: bool = False", "bool", 0): (
        "a segment's custom flag is read only on a note reference, which always sets it"
    ),
    (
        "_Story.mark",
        "def mark(self, kind: str, value: Any, custom: bool = False) -> None:",
        "bool",
        0,
    ): ("the custom flag is read only on a body's note reference, which always passes it"),
    (
        "<module>",
        "section: int = 0",
        "int:0",
        0,
    ): "the one place a paragraph is made sets its section",
    (
        "_note_marks",
        "section = sections[min(paragraph.section, len(sections) - 1)] if sections else None",
        "binop",
        0,
    ): (
        "a paragraph's section is at most the number of sections closed before the last, "
        "len(sections) - 1, so the bound never takes"
    ),
    (
        "_note_marks",
        'paragraph.section if value("numRestart", "continuous") == "eachSect" else None,',
        "str:'continuous'",
        0,
    ): "the default is only compared with 'eachSect', which 'Xontinuous' is not either",
    **{
        ("_Numbering.label", f"{name} = self.{name}.setdefault(key, [None] * 9)", "int:9", 0): (
            "a tenth place is never used: list levels are 0 to 8"
        )
        for name in ("values", "restarts", "showing")
    },
    # certify survivors, part a
    ("_drawn_complex", "0x0590 <= code <= 0x0DFF", "int:3583", 0): (
        "0x0E00, one past the first range, is the start of the second: ending the first there "
        "adds no character"
    ),
    **{
        ("_Story.__init__", "self.unsure: tuple[bool, bool] = (False, False)", "bool", which): (
            "read only in flush_run, which only run calls, after setting it for the run"
        )
        for which in (0, 1)
    },
    # certify survivors, part b
    ("_in_line", "return False", "statement", 0): (
        "its one caller reads it as true or false, and None is as false as False"
    ),
    ("_typed", "return False", "statement", 0): (
        "its callers read it with not, and None is as false as False"
    ),
    ("_typed", 'if found.rsplit("/", 1)[-1] == kind:', "int:1", 0): (
        "splitting once more leaves the last segment, the type's name, the same"
    ),
    ("<module>", "mark_hidden: bool = False", "bool", 0): (
        "the one place a paragraph is made sets whether its mark is hidden"
    ),
    (
        "_unescape.one",
        'digits = (found.group(2) or found.group(3)).lstrip("0") or "0"',
        "str:'0'",
        1,
    ): (
        "a reference to character 0 is refused by the XML parser first (_parse); any other "
        "keeps a digit"
    ),
    ("_canon.add", "continue", "statement", 3): (
        "a change (ins, del, moveFrom, moveTo) is a revision outside _SAME_AS and no cell "
        "change: the test after it passes over it too"
    ),
    ("_run_tokens.walk", "continue", "statement", 3): (
        "the element has no children: walking it reads nothing"
    ),
    ("_agree", "and all(_agree(a, b) for a, b in zip(expected, found, strict=True))", "bool", 0): (
        _STRICT
    ),
    # certify survivors, table style
    ("_Applied.__init__", "for i, found in zip(style_ids, self.styles, strict=True)", "bool", 0): (
        "self.styles is made from style_ids one for one, so zip(strict=True) never raises"
    ),
    (
        "_Applied.at",
        '(self.marked[row] and row >= self.top, "a header row below a row that is none"),',
        "compare:0",
        0,
    ): "the header rows at the top end at the first unmarked row, so row top is never marked",
    (
        "_Applied.at",
        'elif start[0] in ours and self.look[start[1]] and way == "Horz" and self.top > 1:',
        "int:0",
        0,
    ): (
        "for Horz both names are firstRow; for Vert the test of way is false whichever is "
        "looked for"
    ),
}

_COMPARE = {
    ast.Eq: ast.NotEq,
    ast.NotEq: ast.Eq,
    ast.Lt: ast.LtE,
    ast.LtE: ast.Lt,
    ast.Gt: ast.GtE,
    ast.GtE: ast.Gt,
    ast.In: ast.NotIn,
    ast.NotIn: ast.In,
    ast.Is: ast.IsNot,
    ast.IsNot: ast.Is,
}


@dataclass(frozen=True)
class Mutant:
    """One faulty copy of the check: where the fault is, what it is, and the whole source."""

    index: int
    line: int
    kind: str
    source: str
    # The function the fault is in, by its qualified name ("<module>" outside any).
    function: str = "<module>"
    # The line's code, stripped, and which mutant of this function, code and kind it is.
    code: str = ""
    occurrence: int = 0

    @property
    def key(self) -> tuple[str, str, str, int]:
        """The mutant's name in ``EQUIVALENT`` and the record."""
        return (self.function, self.code, self.kind, self.occurrence)


def _functions(tree: ast.Module) -> list[tuple[int, int, str]]:
    """Every function's first and last line and qualified name (Class.method.inner)."""
    out: list[tuple[int, int, str]] = []

    def visit(node: ast.AST, prefix: str) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.FunctionDef | ast.ClassDef):
                name = f"{prefix}{child.name}"
                if isinstance(child, ast.FunctionDef):
                    out.append((child.lineno, child.end_lineno or child.lineno, name))
                visit(child, name + ".")
            else:
                visit(child, prefix)

    visit(tree, "")
    return out


def _function(functions: list[tuple[int, int, str]], line: int) -> str:
    inside = [(first, name) for first, last, name in functions if first <= line <= last]
    return max(inside)[1] if inside else "<module>"


def _docstrings(tree: ast.Module) -> set[int]:
    """The ids of every docstring node, which are left alone."""
    found: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Module | ast.ClassDef | ast.FunctionDef):
            body = node.body
            if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
                found.add(id(body[0].value))
    return found


def _sites(tree: ast.Module) -> Iterator[tuple[ast.AST, str]]:
    """Every place a mutation applies, with what it does, in a fixed order."""
    skip = _docstrings(tree)
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id == "CHECKER_VERSION" for t in node.targets
        ):
            skip.add(id(node.value))
        if isinstance(node, ast.Call) and getattr(node.func, "id", "") == "CertificationError":
            # A refusal's message: what it says, not whether it is made. Other f-strings (an
            # element's path, a namespace) are logic, mutated as any expression is.
            skip |= {id(v) for a in node.args for v in ast.walk(a)}
    for node in ast.walk(tree):
        if id(node) in skip:
            continue
        if isinstance(node, ast.Compare):
            for i, op in enumerate(node.ops):
                if type(op) in _COMPARE:
                    yield node, f"compare:{i}"
        elif isinstance(node, ast.BoolOp):
            yield node, "boolop"
        elif isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not):
            yield node, "drop-not"
        elif isinstance(node, ast.Constant):
            if isinstance(node.value, bool):
                yield node, "bool"
            elif isinstance(node.value, int):
                yield node, f"int:{node.value}"
            elif isinstance(node.value, str) and node.value:
                yield node, f"str:{node.value!r}"
        elif isinstance(node, ast.AugAssign) and isinstance(node.op, ast.Add | ast.Sub):
            yield node, "augassign"
        elif isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add | ast.Sub):
            yield node, "binop"
        elif isinstance(node, ast.Raise | ast.Return | ast.Continue | ast.Break):
            yield node, "statement"
        elif isinstance(node, ast.Expr) and isinstance(node.value, ast.Call):
            yield node, "call"


def _mutate(node: ast.AST, kind: str) -> ast.AST:
    """``node`` with the mutation ``kind`` applied (a fresh node)."""
    new = copy.deepcopy(node)
    if kind.startswith("compare:"):
        i = int(kind.split(":")[1])
        new.ops[i] = _COMPARE[type(new.ops[i])]()  # type: ignore[attr-defined]
    elif kind == "boolop":
        new.op = ast.Or() if isinstance(new.op, ast.And) else ast.And()  # type: ignore[attr-defined]
    elif kind == "drop-not":
        return new.operand  # type: ignore[attr-defined, no-any-return]
    elif kind == "bool":
        new.value = not new.value  # type: ignore[attr-defined]
    elif kind.startswith("int:"):
        new.value = new.value + 1  # type: ignore[attr-defined]
    elif kind.startswith("str:"):
        value: str = new.value  # type: ignore[attr-defined]
        new.value = ("X" if value[0] != "X" else "Y") + value[1:]  # type: ignore[attr-defined]
    elif kind in ("augassign", "binop"):
        new.op = ast.Sub() if isinstance(new.op, ast.Add) else ast.Add()  # type: ignore[attr-defined]
    elif kind in ("statement", "call"):
        return ast.Pass()
    return new


def _swap(parents: dict[int, tuple[ast.AST, str, int | None]], old: ast.AST, new: ast.AST) -> None:
    """Put ``new`` where ``old`` stands in its parent."""
    parent, name, index = parents[id(old)]
    if index is None:
        setattr(parent, name, new)
    else:
        getattr(parent, name)[index] = new


def count(target: Path = TARGET) -> int:
    """How many mutants ``mutants`` makes: one per site, without making them."""
    return len(list(_sites(ast.parse(target.read_text("utf-8")))))


def keys(target: Path = TARGET) -> list[tuple[str, str, str, int]]:
    """Every mutant's key, without making the mutants."""
    return [mutant.key for mutant in mutants(target, sources=False)]


def mutants(target: Path = TARGET, sources: bool = True) -> list[Mutant]:
    """Every mutant of the check, each a whole module's source.

    The source is parsed once; each fault is put in its place, the module written out, and the
    original put back.
    """
    text = target.read_text("utf-8")
    lines = text.splitlines()
    tree = ast.parse(text)
    functions = _functions(tree)
    parents: dict[int, tuple[ast.AST, str, int | None]] = {}
    for parent in ast.walk(tree):
        for name, value in ast.iter_fields(parent):
            if isinstance(value, ast.AST):
                parents[id(value)] = (parent, name, None)
            elif isinstance(value, list):
                for index, item in enumerate(value):
                    if isinstance(item, ast.AST):
                        parents[id(item)] = (parent, name, index)
    sites = list(_sites(tree))
    # Each mutant's name: its function, its line's code and its kind.
    names = []
    for node, kind in sites:
        line = getattr(node, "lineno", 0)
        names.append((_function(functions, line), lines[line - 1].strip(), kind))
    # Which mutant of its name each is, in source order.
    where = [(getattr(n, "lineno", 0), getattr(n, "col_offset", 0), k) for n, k in sites]
    occurrence = [0] * len(sites)
    seen: dict[tuple[str, str, str], int] = {}
    for index in sorted(range(len(sites)), key=where.__getitem__):
        occurrence[index] = seen.get(names[index], 0)
        seen[names[index]] = occurrence[index] + 1
    out: list[Mutant] = []
    for index, (node, kind) in enumerate(sites):
        source = ""
        if sources:
            _swap(parents, node, _mutate(node, kind))
            source = ast.unparse(tree)
            _swap(parents, node, node)
        function, code, _ = names[index]
        line = getattr(node, "lineno", 0)
        out.append(Mutant(index, line, kind, source, function, code, occurrence[index]))
    return out


def _pytest(
    tests: list[str], folder: str, root: Path = ROOT
) -> subprocess.CompletedProcess[str] | None:
    """Run ``tests`` of ``root`` on the package in ``folder``, stopping at the first failure.

    None: a hang.
    """
    environment = {**os.environ, "PYTHONPATH": folder, "PYTHONDONTWRITEBYTECODE": "1"}
    command = [sys.executable, "-m", "pytest", "-x", "-q", "-rf", "-p", "no:cacheprovider"]
    try:
        return subprocess.run(
            [*command, *tests],
            cwd=root,
            env=environment,
            capture_output=True,
            text=True,
            timeout=300,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return None


def _run(
    mutant: Mutant, package: Path, hint: str | None = None, root: Path = ROOT
) -> tuple[bool, str | None]:
    """Whether the tests of ``root`` kill ``mutant`` (fail, error or hang), and the one that did.

    ``hint``, the test that killed this mutant before, is run first, alone: if it fails, the
    mutant is killed. Otherwise every test is run, as without a hint. So a hint only saves time:
    a mutant survives only if every test passes.
    """
    with tempfile.TemporaryDirectory() as folder:
        copied = Path(folder) / "label_docx"
        shutil.copytree(package, copied)
        (copied / "certify.py").write_text(mutant.source, "utf-8")
        if hint is not None:
            done = _pytest([hint], folder, root)
            # 1: a test failed. Anything else (passed, no such test now, a usage error) is not
            # taken as a kill.
            if done is None or done.returncode == 1:
                return True, hint
        done = _pytest(TESTS, folder, root)
        if done is None:
            return True, None
        failed = re.search(r"^FAILED (\S+)", done.stdout, re.MULTILINE)
        return done.returncode != 0, failed.group(1) if failed else None


# Everything a mutant's result can depend on: copied at the start of a run, and run on.
COPIED = ("src", "tests", "scripts", "corpus", "pyproject.toml")


def _digest(root: Path, names: tuple[str, ...] | list[str]) -> str:
    """SHA-256 of the files at ``names`` under ``root`` (files or folders), by path and content."""
    digest = hashlib.sha256()
    for name in names:
        found = (root / name).rglob("*") if (root / name).is_dir() else [root / name]
        for path in sorted(found):
            if path.is_file() and "__pycache__" not in path.parts:
                digest.update(str(path.relative_to(root)).encode() + b"\0")
                digest.update(hashlib.sha256(path.read_bytes()).digest())
    return digest.hexdigest()


def fingerprint(root: Path = ROOT) -> str:
    """SHA-256 of every file a mutant's result can depend on, by path and content."""
    return _digest(root, COPIED)


def held_sha256(root: Path = ROOT) -> str:
    """SHA-256 of the check's tests and all they run on (the package, their helpers, corpus)."""
    helpers = [
        "tests/test_reader.py",
        "scripts/fuzz_docx.py",
        "scripts/lock.py",
        "scripts/numbering_cases.py",
        "pyproject.toml",
        "src",
    ]
    return _digest(root, [*TESTS, *helpers, "corpus", "tests/data"])


def _copy(root: Path) -> None:
    """Copy everything in ``COPIED`` into ``root``."""
    for name in COPIED:
        if (ROOT / name).is_dir():
            shutil.copytree(ROOT / name, root / name, ignore=shutil.ignore_patterns("__pycache__"))
        else:
            shutil.copy2(ROOT / name, root / name)


def _progress(key: str) -> dict[int, bool]:
    """The results kept from earlier parts of a run with this fingerprint, by mutant index."""
    try:
        kept = json.loads(PROGRESS.read_text("utf-8"))
    except OSError, ValueError:
        return {}
    if kept.get("fingerprint") != key:
        return {}
    return {int(index): bool(dead) for index, dead in kept["killed"].items()}


def _keep(key: str, results: dict[int, bool]) -> None:
    """Write the results so far, whole, by replacing the file."""
    partial = PROGRESS.with_suffix(".tmp")
    payload = {"fingerprint": key, "killed": {str(i): d for i, d in sorted(results.items())}}
    partial.write_text(json.dumps(payload) + "\n", "utf-8")
    partial.replace(PROGRESS)


def _classify(survivors: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The survivors with the reason each cannot matter, where one is recorded."""
    return [
        {**s, "equivalent": EQUIVALENT.get((s["function"], s["code"], s["kind"], s["occurrence"]))}
        for s in survivors
    ]


def _report(survivors: list[dict[str, Any]], killed: int, total: int) -> int:
    for s in survivors:
        status = f"equivalent: {s['equivalent']}" if s["equivalent"] else "SURVIVED"
        sys.stdout.write(
            f"  {s['function']} line {s['line']} {s['kind']}: {s['code']}  [{status}]\n"
        )
    unexplained = [s for s in survivors if not s["equivalent"]]
    sys.stdout.write(
        f"killed {killed} of {total}; {len(survivors) - len(unexplained)} equivalent; "
        f"{len(unexplained)} unexplained\n"
    )
    return 1 if unexplained else 0


def main() -> int:
    """Run every mutant; 1 if one survives that is not recorded as equivalent."""
    parser = argparse.ArgumentParser(description="Mutation testing of the conservation check.")
    parser.add_argument("--write", action="store_true", help="write docs/checker-mutants.json")
    parser.add_argument(
        "--reclassify",
        action="store_true",
        help="apply EQUIVALENT to the recorded run of this certify.py, without running it again",
    )
    parser.add_argument("--jobs", type=int, default=os.cpu_count() or 4)
    parser.add_argument(
        "--budget", type=float, help="minutes after which no mutant is started (then exit 3)"
    )
    args = parser.parse_args()
    if args.reclassify:
        record = json.loads(RECORD.read_text("utf-8"))
        if record["targetSha256"] != hashlib.sha256(TARGET.read_bytes()).hexdigest():
            raise SystemExit("certify.py changed since the recorded run: run it again")
        record["survivors"] = _classify(record["survivors"])
        RECORD.write_text(json.dumps(record, indent=2, sort_keys=True) + "\n", "utf-8")
        return _report(record["survivors"], record["killed"], record["mutants"])
    deadline = None if args.budget is None else time.monotonic() + args.budget * 60
    with tempfile.TemporaryDirectory() as folder:
        # The run is of this copy: an edit made (or undone) meanwhile does not reach it.
        root = Path(folder)
        _copy(root)
        key = fingerprint(root)
        if key != fingerprint():
            raise SystemExit("files changed while they were copied: run again")
        return _mutate_all(root, key, args.jobs, deadline, args.write)


def _mutate_all(root: Path, key: str, jobs: int, deadline: float | None, write: bool) -> int:
    """Run every mutant of the copy at ``root`` (fingerprint ``key``); write the record if asked."""
    target = root / TARGET.relative_to(ROOT)
    package = target.parent
    # The tests must pass on the check as it is; else every fault would look caught.
    unaltered = Mutant(-1, 0, "none", target.read_text("utf-8"))
    if _run(unaltered, package, root=root)[0]:
        raise SystemExit("the tests fail on the check as it is: fix them before mutating it")
    every = mutants(target)
    results = _progress(key)
    sys.stdout.write(
        f"{len(every)} mutants of {TARGET.relative_to(ROOT)}; {len(results)} kept from before\n"
    )
    sys.stdout.flush()
    lock = threading.Lock()

    try:
        killers: dict[str, str] = json.loads(KILLERS.read_text("utf-8"))
    except OSError, ValueError:
        killers = {}

    def run(mutant: Mutant) -> None:
        if mutant.index in results or (deadline is not None and time.monotonic() > deadline):
            return
        name = "\0".join((*mutant.key[:3], str(mutant.occurrence)))
        dead, killer = _run(mutant, package, killers.get(name), root)
        with lock:
            results[mutant.index] = dead
            _keep(key, results)
            if killer is not None:
                killers[name] = killer
                # Whole or not at all, as the progress is: a hint lost costs only time.
                partial = KILLERS.with_suffix(".tmp")
                partial.write_text(json.dumps(killers, sort_keys=True) + "\n", "utf-8")
                partial.replace(KILLERS)
            if len(results) % 50 == 0:
                sys.stdout.write(f"  {len(results)} of {len(every)} run\n")
                sys.stdout.flush()

    with concurrent.futures.ThreadPoolExecutor(jobs) as pool:
        list(pool.map(run, every))
    left = len(every) - len(results)
    if left:
        sys.stdout.write(f"unfinished: {left} of {len(every)} mutants left; run again\n")
        return UNFINISHED
    killed = [results[mutant.index] for mutant in every]
    survivors = _classify(
        [
            {
                "function": mutant.function,
                "line": mutant.line,
                "kind": mutant.kind,
                "code": mutant.code,
                "occurrence": mutant.occurrence,
            }
            for mutant, dead in zip(every, killed, strict=True)
            if not dead
        ]
    )
    if write:
        # The results are of the copy; the record is of the files as they are now, so they
        # must be the same. (The progress stays: it is of the copy, by its fingerprint.)
        if fingerprint() != key:
            raise SystemExit("files changed during the run: the record is not written")
        record = {
            "target": str(TARGET.relative_to(ROOT)),
            "targetSha256": hashlib.sha256(target.read_bytes()).hexdigest(),
            "tests": TESTS,
            "testsSha256": held_sha256(root),
            "mutants": len(every),
            "killed": sum(killed),
            "survivors": survivors,
        }
        RECORD.write_text(json.dumps(record, indent=2, sort_keys=True) + "\n", "utf-8")
        sys.stdout.write(f"wrote {RECORD.relative_to(ROOT)}\n")
        PROGRESS.unlink(missing_ok=True)
    return _report(survivors, sum(killed), len(every))


if __name__ == "__main__":
    raise SystemExit(main())
