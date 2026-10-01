"""Mutation testing of the conservation check: does every fault in it make a test fail?

    uv run --frozen python scripts/mutate_checker.py          # run, print the survivors
    uv run --frozen python scripts/mutate_checker.py --write  # and write the record

The check (``src/label_docx/certify.py``) is what the proof rests on, so its tests must hold
it, not merely run it. This script makes one small fault at a time in a copy of it: a comparison
turned round (``==`` to ``!=``, ``<`` to ``<=``, ``in`` to ``not in``), ``and`` to ``or``, a
``not`` dropped, a number one off, a ``True`` made ``False``, one character of a string made
another, an addition made a subtraction, a ``raise``, ``return``, ``continue`` or ``break``
removed or a refusal made to pass, a call's result discarded. Each faulty copy is run against
the check's tests (``tests/test_certify.py``, ``tests/test_robustness.py``,
``tests/test_output.py``); a fault the tests notice is killed. A fault that survives is either
a missing test, to be added, or a change that cannot alter what the check does (an equivalent
mutant), to be recorded in ``EQUIVALENT`` below with the reason. ``tests/test_checker_mutants.py``
holds the recorded run: every mutant killed or recorded as equivalent.
"""

from __future__ import annotations

import argparse
import ast
import concurrent.futures
import copy
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / "src" / "label_docx" / "certify.py"
RECORD = ROOT / "docs" / "checker-mutants.json"
TESTS = [
    "tests/test_certify.py",
    "tests/test_headers_comments.py",
    "tests/test_robustness.py",
    "tests/test_output.py",
]

_SYMBOL_RANGE = "low = code - 0xF000 if 0xF000 <= code <= 0xF0FF else code"
_TABLE_EDGE = (
    "the Symbol table holds neither 0x00 nor 0xFF nor 0x100: a code at either edge of the "
    "U+F000 range, or just past it, is refused before and after"
)
_STRICT = "the lengths are compared just before, so zip(strict=True) never raises"
_LEDGER = (
    "the counts follow from the sequences, which are equal by then: the ledger is a second "
    "statement of the same fact, stated in the certificate"
)
_NOTE = "a marker's label is compared only with 'page'; any other is a note mark"
_ROW_GROUP = (
    "a row group is always met beside a table or a row, which end the paragraph; text directly "
    "in one is refused by the reader"
)
_SECTIONS_ZIP = (
    'for index, (mine, theirs) in enumerate(zip(self.sections, value["sections"], strict=True)):'
)
_SYMBOL_COUNT = "self.ledger.symbol += sum(1 for a, b in zip(text, mapped, strict=True) if a != b)"
# Mutants that cannot change what the check does, by function, line and mutation, each with the
# reason. A survivor not listed here fails the run.
EQUIVALENT: dict[tuple[str, str, str], str] = {
    ("_local", 'return tag.rsplit("}", 1)[-1]', "int:1"): (
        "an element's tag holds one '}', after its namespace"
    ),
    (
        "_on",
        'return element.get(_w("val"), "true").lower() not in ("0", "false", "off")',
        "str:'true'",
    ): "any value outside the off list is on: 'Xrue' is on as 'true' is",
    ("_Fonts.font", "return None", "statement"): "the function returns None at its end anyway",
    ("_symbol_reading", _SYMBOL_RANGE, "compare:0"): _TABLE_EDGE,
    ("_symbol_reading", _SYMBOL_RANGE, "compare:1"): _TABLE_EDGE,
    ("_symbol_reading", _SYMBOL_RANGE, "int:61440"): _TABLE_EDGE,
    ("_symbol_reading", _SYMBOL_RANGE, "int:61695"): _TABLE_EDGE,
    ("_Story.token", _SYMBOL_RANGE, "compare:0"): _TABLE_EDGE,
    ("_Story.token", _SYMBOL_RANGE, "compare:1"): _TABLE_EDGE,
    ("_Story.token", _SYMBOL_RANGE, "int:61440"): _TABLE_EDGE,
    ("_Story.token", _SYMBOL_RANGE, "int:61695"): _TABLE_EDGE,
    ("_Story.token", _SYMBOL_COUNT, "bool"): (
        "the Symbol table maps one character to one, so the two are the same length"
    ),
    ("_Story.inline", 'if child.tag == _w("pPr"):', "str:'pPr'"): (
        "a paragraph's properties are walked as a container instead: they hold no run, and text "
        "in them is refused either way"
    ),
    ("_Story.inline", "continue", "statement"): (
        "a paragraph's properties are walked as a container instead: they hold no run, and text "
        "in them is refused either way"
    ),
    ("_Story.run", 'self.mark("note", (kind, self.story[1]))', "str:'note'"): _NOTE,
    ("_Story.run", 'self.mark("note", (kind, int(child.get(_w("id"), ""))))', "str:'note'"): _NOTE,
    ("_Story.flush_run", "return", "statement"): (
        "an empty run then adds an empty segment, or no hidden characters: nothing either way"
    ),
    ("DocxSource.__init__", 'if note.get(_w("type"), "normal") in _NOTE_LAYOUT:', "str:'normal'"): (
        "the default only has to be outside the layout types, as 'Xormal' is"
    ),
    (
        "DocxSource.certify",
        'raise CertificationError("the ledger does not balance")',
        "statement",
    ): (_LEDGER),
    ("<module>", '"thead",', "str:'thead'"): _ROW_GROUP,
    ("<module>", '"tbody",', "str:'tbody'"): _ROW_GROUP,
    ("_Div.__init__", "self.pending = False", "bool"): (
        "the parser starts at the div, a block, which ends the (empty) paragraph and clears it"
    ),
    ("_Div.handle_starttag", "self.open_tables.append([self.tables, -1, -1])", "int:1"): (
        "the cell count starts again at each row; only the row count's start matters, and its "
        "mutant is caught"
    ),
    ("EpiSource._section", "parsed.close()", "call"): (
        "the div ends with its own end tag, so nothing is left buffered"
    ),
    ("EpiSource._section", "parsed.flush()", "call"): (
        "the div's end tag, a block's, has ended the last paragraph"
    ),
    ("EpiSource.certify.section", "if a != b", "compare:0"): (
        "where a difference is found only words the refusal"
    ),
    ("EpiSource.certify.section", 'zip(mine.sections, theirs["sections"], strict=True)', "bool"): (
        _STRICT
    ),
    ("EpiSource.certify", _SECTIONS_ZIP, "bool"): _STRICT,
    ("_refused", "return False", "statement"): (
        "a function that ends without a return gives None, which is as false as False here"
    ),
    (
        "DocxSource.certify",
        '_paragraphs(self.body, value["paragraphs"], "paragraph")',
        "str:'paragraph'",
    ): "the word only names the place in a refusal's detail",
    (
        "DocxSource.certify",
        'for (comment, part), theirs in zip(self.comments, value["comments"], strict=True):',
        "bool",
    ): "the comments' metadata lists are compared just before, so the lengths are equal",
    (
        "DocxSource.certify",
        "for (name, _, part), story in zip(found, theirs, strict=True):",
        "bool",
    ): "the parts and their uses are compared just before, so the lengths are equal",
    (
        "DocxSource._pairs",
        'for (comment, part), theirs in zip(self.comments, value["comments"], strict=True):',
        "bool",
    ): "called only after certify has compared the lengths",
    (
        "DocxSource._pairs",
        'for (name, _, part), story in zip(found, value[kind + "s"], strict=True):',
        "bool",
    ): "called only after certify has compared the lengths",
    (
        "_paragraphs",
        "for index, (mine, paragraph) in enumerate(zip(part.paragraphs, theirs, strict=True)):",
        "bool",
    ): _STRICT,
    ("EpiSource.certify", 'raise CertificationError("the ledger does not balance")', "statement"): (
        _LEDGER
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
        if isinstance(node, ast.JoinedStr):
            # An f-string's parts are messages: what a refusal says, not whether it is made.
            skip |= {id(v) for v in ast.walk(node)}
        if isinstance(node, ast.Call) and getattr(node.func, "id", "") == "CertificationError":
            skip |= {id(a) for a in node.args}
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


class _Replace(ast.NodeTransformer):
    def __init__(self, target: ast.AST, replacement: ast.AST) -> None:
        self.target, self.replacement = target, replacement

    def visit(self, node: ast.AST) -> ast.AST:
        if node is self.target:
            return self.replacement
        visited: ast.AST = super().visit(node)
        return visited


def mutants() -> list[Mutant]:
    """Every mutant of the check, each a whole module's source."""
    text = TARGET.read_text("utf-8")
    out: list[Mutant] = []
    functions = _functions(ast.parse(text))
    count = len(list(_sites(ast.parse(text))))
    for index in range(count):
        tree = ast.parse(text)
        node, kind = list(_sites(tree))[index]
        mutated = _Replace(node, _mutate(node, kind)).visit(tree)
        ast.fix_missing_locations(mutated)
        source = ast.unparse(mutated)
        line = getattr(node, "lineno", 0)
        out.append(Mutant(index, line, kind, source, _function(functions, line)))
    return out


def _run(mutant: Mutant, package: Path) -> bool:
    """Whether the tests kill ``mutant``: fail, error or hang."""
    with tempfile.TemporaryDirectory() as folder:
        copied = Path(folder) / "label_docx"
        shutil.copytree(package, copied)
        (copied / "certify.py").write_text(mutant.source, "utf-8")
        environment = {**os.environ, "PYTHONPATH": folder, "PYTHONDONTWRITEBYTECODE": "1"}
        try:
            done = subprocess.run(
                [sys.executable, "-m", "pytest", "-x", "-q", "-p", "no:cacheprovider", *TESTS],
                cwd=ROOT,
                env=environment,
                capture_output=True,
                timeout=300,
                check=False,
            )
        except subprocess.TimeoutExpired:
            return True
        return done.returncode != 0


def _classify(survivors: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The survivors with the reason each cannot matter, where one is recorded."""
    return [
        {**s, "equivalent": EQUIVALENT.get((s["function"], s["code"], s["kind"]))}
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
    args = parser.parse_args()
    if args.reclassify:
        record = json.loads(RECORD.read_text("utf-8"))
        if record["targetSha256"] != hashlib.sha256(TARGET.read_bytes()).hexdigest():
            raise SystemExit("certify.py changed since the recorded run: run it again")
        record["survivors"] = _classify(record["survivors"])
        RECORD.write_text(json.dumps(record, indent=2, sort_keys=True) + "\n", "utf-8")
        return _report(record["survivors"], record["killed"], record["mutants"])
    lines = TARGET.read_text("utf-8").splitlines()
    every = mutants()
    sys.stdout.write(f"{len(every)} mutants of {TARGET.relative_to(ROOT)}\n")
    package = TARGET.parent
    with concurrent.futures.ThreadPoolExecutor(args.jobs) as pool:
        killed = list(pool.map(lambda m: _run(m, package), every))
    survivors = _classify(
        [
            {
                "function": mutant.function,
                "line": mutant.line,
                "kind": mutant.kind,
                "code": lines[mutant.line - 1].strip(),
            }
            for mutant, dead in zip(every, killed, strict=True)
            if not dead
        ]
    )
    if args.write:
        record = {
            "target": str(TARGET.relative_to(ROOT)),
            "targetSha256": hashlib.sha256(TARGET.read_bytes()).hexdigest(),
            "tests": TESTS,
            "mutants": len(every),
            "killed": sum(killed),
            "survivors": survivors,
        }
        RECORD.write_text(json.dumps(record, indent=2, sort_keys=True) + "\n", "utf-8")
        sys.stdout.write(f"wrote {RECORD.relative_to(ROOT)}\n")
    return _report(survivors, sum(killed), len(every))


if __name__ == "__main__":
    raise SystemExit(main())
