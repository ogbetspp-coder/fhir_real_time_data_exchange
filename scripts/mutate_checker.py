"""Mutation testing of the conservation check: does every fault in it make a test fail?

    uv run --frozen python scripts/mutate_checker.py          # run, print the survivors
    uv run --frozen python scripts/mutate_checker.py --write  # and write the record
    uv run --frozen python scripts/mutate_checker.py --write --budget 100  # in parts

A run takes about half an hour on eight cores, so each mutant's result is kept as it comes in
(``.mutants-progress.json``), with a fingerprint of everything a result depends on: the
package, the tests, the scripts and the corpus. With ``--budget MINUTES`` a run starts no
mutant after that time, keeps what it has and stops (exit 3); the next run takes up where it
stopped. If anything in the fingerprint changed, every mutant is run again.

The check (``src/label_docx/certify.py``) is what the proof rests on, so its tests must hold
it, not merely run it. This script makes one small fault at a time in a copy of it: a comparison
turned round (``==`` to ``!=``, ``<`` to ``<=``, ``in`` to ``not in``), ``and`` to ``or``, a
``not`` dropped, a number one off, a ``True`` made ``False``, one character of a string made
another, an addition made a subtraction, a ``raise``, ``return``, ``continue`` or ``break``
removed or a refusal made to pass, a call's result discarded. Each faulty copy is run against
the check's tests (``TESTS``); a fault the tests notice is killed, and the test that last killed
it is tried first next time. A fault that survives is either
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
_XML_LINE = '_XML_NS = "http://www.w3.org/XML/1998/namespace"'
_XML_PREFIX = "the xml prefix can never name Word's namespace, the only one the tokenizer looks for"
_FIRST_LESS = "a '<' at the very start finds no capture open, so the chunk is never used there"
_NO_DEFAULT = (
    "a character style falls back to no default, as an unknown kind does: Word applies no "
    "default character style to text"
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
_TABLE_STEP = (
    "the table count is compared only between two walks that both make it: any step but zero "
    "numbers the same tables apart, so a table added or lost shows either way"
)
EQUIVALENT: dict[tuple[str, str, str], str] = {
    ("_run_tokens.walk", "continue", "statement"): (
        "a paragraph holds no paragraph and run content no run the walk reads: walking either"
        " again only adds to a list outside any paragraph, which is never read"
    ),
    (
        "_run_tokens.walk",
        'gone = dropped or (child.tag in dropping and element.tag != _w("rPr"))',
        "str:'rPr'",
    ): "a paragraph mark's change markers are empty: dropping one drops no content",
    ("_run_tokens.table", "index, tables = tables, tables + 1", "binop"): _TABLE_STEP,
    ("_run_tokens.table", "index, tables = tables, tables + 1", "int:1"): _TABLE_STEP,
    ("_raw_texts", "found = _TAG.match(text, less + 1)", "int:1"): (
        "a tag's first character is its name's, never '>' or a quote, so matching from one "
        "character later finds the same end"
    ),
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
        "_Fonts.levels",
        'found += self.chain(None if style is None else style.get(_w("val")), "character")',
        "str:'character'",
    ): _NO_DEFAULT,
    (
        "_Fonts.marks",
        'self.chain(None if style is None else style.get(_w("val")), "character"),',
        "str:'character'",
    ): _NO_DEFAULT,
    ("_Fonts.marks.nearest", "return None", "statement"): (
        "the function returns None at its end anyway"
    ),
    (
        "_match",
        "at = next(i for i, (a, b) in enumerate(zip(shown, claimed, strict=True)) if set(a) != b)",
        "bool",
    ): "both lists have one entry per character of the text, which was compared just before",
    ("<module>", _XML_LINE, "str:'http://www.w3.org/XML/1998/namespace'"): _XML_PREFIX,
    ("_raw_texts", 'scopes: list[dict[str, str]] = [{"xml": _XML_NS}]', "str:'xml'"): _XML_PREFIX,
    ("_raw_texts", "depth_of_capture = -1", "int:1"): (
        "the depth is set whenever a capture begins, before it is read"
    ),
    ("_raw_texts", "chunk = text[at:] if less < 0 else text[at:less]", "compare:0"): _FIRST_LESS,
    ("_raw_texts", "chunk = text[at:] if less < 0 else text[at:less]", "int:0"): _FIRST_LESS,
    ("_raw_texts", 'name = body.split(None, 1)[0] if body.strip() else ""', "int:1"): (
        "splitting once more leaves the first word, the name, the same"
    ),
    (
        "_paragraphs",
        "for index, (mine, paragraph) in enumerate(zip(part.paragraphs, theirs, strict=True)):",
        "bool",
    ): _STRICT,
    ("EpiSource.certify", 'raise CertificationError("the ledger does not balance")', "statement"): (
        _LEDGER
    ),
    ("<module>", "custom: bool = False", "bool"): (
        "a segment's custom flag is read only on a note reference, which always sets it"
    ),
    (
        "_Story.mark",
        "def mark(self, kind: str, value: Any, custom: bool = False) -> None:",
        "bool",
    ): ("the custom flag is read only on a body's note reference, which always passes it"),
    ("<module>", "section: int = 0", "int:0"): "the one place a paragraph is made sets its section",
    (
        "_note_marks",
        "section = sections[min(paragraph.section, len(sections) - 1)] if sections else None",
        "binop",
    ): (
        "a paragraph's section is at most the number of sections closed before the last, "
        "len(sections) - 1, so the bound never takes"
    ),
    (
        "_note_marks",
        'paragraph.section if value("numRestart", "continuous") == "eachSect" else None,',
        "str:'continuous'",
    ): "the default is only compared with 'eachSect', which 'Xontinuous' is not either",
    (
        "DocxSource.certify",
        "for index, (label, theirs) in enumerate("
        'zip(self.labels, value["paragraphs"], strict=True)):',
        "bool",
    ): _STRICT,
    **{
        ("_Numbering.label", f"{name} = self.{name}.setdefault(key, [None] * 9)", "int:9"): (
            "a tenth place is never used: list levels are 0 to 8"
        )
        for name in ("values", "restarts", "showing")
    },
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


def count() -> int:
    """How many mutants ``mutants`` makes: one per site, without making them."""
    return len(list(_sites(ast.parse(TARGET.read_text("utf-8")))))


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


def _pytest(tests: list[str], folder: str) -> subprocess.CompletedProcess[str] | None:
    """Run ``tests`` on the package in ``folder``, stopping at the first failure; None: a hang."""
    environment = {**os.environ, "PYTHONPATH": folder, "PYTHONDONTWRITEBYTECODE": "1"}
    command = [sys.executable, "-m", "pytest", "-x", "-q", "-rf", "-p", "no:cacheprovider"]
    try:
        return subprocess.run(
            [*command, *tests],
            cwd=ROOT,
            env=environment,
            capture_output=True,
            text=True,
            timeout=300,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return None


def _run(mutant: Mutant, package: Path, hint: str | None = None) -> tuple[bool, str | None]:
    """Whether the tests kill ``mutant`` (fail, error or hang), and the test that failed.

    ``hint``, the test that killed this mutant before, is run first, alone: if it fails, the
    mutant is killed. Otherwise every test is run, as without a hint. So a hint only saves time:
    a mutant survives only if every test passes.
    """
    with tempfile.TemporaryDirectory() as folder:
        copied = Path(folder) / "label_docx"
        shutil.copytree(package, copied)
        (copied / "certify.py").write_text(mutant.source, "utf-8")
        if hint is not None:
            done = _pytest([hint], folder)
            # 1: a test failed. Anything else (passed, no such test now, a usage error) is not
            # taken as a kill.
            if done is None or done.returncode == 1:
                return True, hint
        done = _pytest(TESTS, folder)
        if done is None:
            return True, None
        failed = re.search(r"^FAILED (\S+)", done.stdout, re.MULTILINE)
        return done.returncode != 0, failed.group(1) if failed else None


def fingerprint() -> str:
    """SHA-256 of every file a mutant's result can depend on, by path and content."""
    digest = hashlib.sha256()
    for folder in ("src", "tests", "scripts", "corpus"):
        for path in sorted((ROOT / folder).rglob("*")):
            if path.is_file() and "__pycache__" not in path.parts:
                digest.update(str(path.relative_to(ROOT)).encode() + b"\0")
                digest.update(hashlib.sha256(path.read_bytes()).digest())
    return digest.hexdigest()


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
    lines = TARGET.read_text("utf-8").splitlines()
    key = fingerprint()
    # The tests must pass on the check as it is; else every fault would look caught.
    unaltered = Mutant(-1, 0, "none", TARGET.read_text("utf-8"))
    if _run(unaltered, TARGET.parent)[0]:
        raise SystemExit("the tests fail on the check as it is: fix them before mutating it")
    every = mutants()
    results = _progress(key)
    sys.stdout.write(
        f"{len(every)} mutants of {TARGET.relative_to(ROOT)}; {len(results)} kept from before\n"
    )
    sys.stdout.flush()
    package = TARGET.parent
    lock = threading.Lock()

    try:
        killers: dict[str, str] = json.loads(KILLERS.read_text("utf-8"))
    except OSError, ValueError:
        killers = {}

    def run(mutant: Mutant) -> None:
        if mutant.index in results or (deadline is not None and time.monotonic() > deadline):
            return
        name = "\0".join((mutant.function, lines[mutant.line - 1].strip(), mutant.kind))
        dead, killer = _run(mutant, package, killers.get(name))
        with lock:
            results[mutant.index] = dead
            _keep(key, results)
            if killer is not None:
                killers[name] = killer
                KILLERS.write_text(json.dumps(killers, sort_keys=True) + "\n", "utf-8")
            if len(results) % 50 == 0:
                sys.stdout.write(f"  {len(results)} of {len(every)} run\n")
                sys.stdout.flush()

    with concurrent.futures.ThreadPoolExecutor(args.jobs) as pool:
        list(pool.map(run, every))
    if key != fingerprint():
        raise SystemExit("files changed during the run: its results are not of one state")
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
        PROGRESS.unlink(missing_ok=True)
    return _report(survivors, sum(killed), len(every))


if __name__ == "__main__":
    raise SystemExit(main())
