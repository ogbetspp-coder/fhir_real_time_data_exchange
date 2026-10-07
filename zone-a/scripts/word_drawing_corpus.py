"""The Word drawing's step 1 on folders of Word labels: counts, codes and times, never text.

    uv run --frozen python scripts/word_drawing_corpus.py parity smpc FOLDER SHELL > smpc.jsonl
    uv run --frozen python scripts/word_drawing_corpus.py parity pl FOLDER SHELL > pl.jsonl
    uv run --frozen python scripts/word_drawing_corpus.py repeat SHELL smpc.jsonl pl.jsonl > r.jsonl
    uv run --frozen python scripts/word_drawing_corpus.py summary smpc.jsonl pl.jsonl r.jsonl

``docs/design/certified-word-drawing.md``, "Step 1: measured". SHELL is a chrome-headless-shell; a
label is named by its position in its folder (sorted by name), never by its name, so the output
can be kept where the labels may not go.

``parity``: one label at a time, each in a process of its own, so its peak memory is its own. It
is read (by its accepted view where it is tracked, after the read without a view is refused), cut
into SmPCs or leaflets (``zone_a.structure.smpcs``, ``zone_a.leaflet.leaflets``), and each part
structured with no heading assigned and built. Each part that builds is drawn by
``zone_a.drawing.check`` four times: in Google Chrome (``--chrome``, else where
``label_docx.browser`` finds it), in SHELL, in SHELL again, and in SHELL at 375 by 812 pixels and a
device pixel ratio of 2, through a launcher this script writes that adds those two switches
(``label_docx.browser`` is not changed). Chrome's raw answers (``browser_sections`` and
``browser_markers``) are compared by digest, and every narrative is parsed by HTML and by XML in
SHELL and in Google Chrome (``word_drawing_check.parsed_alike``). One JSON line per label.

``repeat``: ``python -m zone_a.drawing`` with SHELL on every label it can sign: each part of the
parity results that carries every section, and each committed certified Word label it signs, with
its own request. Each runs ``--runs`` times one after another and ``--runs`` times two at a time
(20 by default). One JSON line per label: the statuses, how many different outputs, the closed
codes on standard error, the times.

``summary``: the counts the design note gives, from those lines.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import resource
import statistics
import subprocess
import sys
import tempfile
import time
from collections import Counter
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import ModuleType
from typing import Any, Final

from label_docx import browser
from label_docx.reader import DocxRefusedError

from zone_a import drawing, leaflet, recompute, structure, word_epi
from zone_a.canonical_json import canonical_json
from zone_a.certified import Body, read_body

ROOT: Final = Path(__file__).resolve().parents[2]
COMMITTED: Final = ROOT / "test" / "fixtures" / "certified-word" / "recompute"
WINDOW: Final = ("--window-size=375,812", "--force-device-scale-factor=2")
# ru_maxrss is in bytes on macOS and in KiB on Linux.
_MAXRSS_BYTES: Final = 1 if sys.platform == "darwin" else 1024


def _check_script() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "word_drawing_check", Path(__file__).with_name("word_drawing_check.py")
    )
    if spec is None or spec.loader is None:
        raise SystemExit("scripts/word_drawing_check.py is missing")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def window_launcher(shell: Path, folder: Path) -> Path:
    """A launcher, written in ``folder``, that runs ``shell`` at 375 by 812 and a ratio of 2."""
    launcher = folder / "window.sh"
    launcher.write_text(f'#!/bin/sh\nexec "{shell}" {" ".join(WINDOW)} "$@"\n', "utf-8")
    launcher.chmod(0o755)
    return launcher


def _read(data: bytes) -> tuple[Body, str | None]:
    try:
        return read_body(data), None
    except DocxRefusedError as refused:
        if refused.code != "tracked-change":
            raise
    return read_body(data, "accepted"), "accepted"


def _files(folder: Path) -> list[Path]:
    return sorted(folder.glob("*.docx"))


def _peak_mb(who: int) -> int:
    return round(resource.getrusage(who).ru_maxrss * _MAXRSS_BYTES / 2**20)


def _parts(document: str, body: Body) -> Iterator[tuple[int, dict[str, Any], dict[str, Any]]]:
    """Each part's index, structure and registry; none where the document cannot be cut."""
    registry, mapping = recompute._load(document, ROOT)
    if document == "pl":
        parts, why = leaflet.leaflets(body.paragraphs, registry)
    else:
        parts, why = structure.smpcs(body.paragraphs, registry, mapping)
    if why is not None:
        return
    for n, part in enumerate(parts):
        if document == "pl":
            structured = leaflet.structure(body.paragraphs, registry, mapping, None, part)
        else:
            span = part if len(parts) > 1 else None
            structured = structure.structure(body.paragraphs, registry, mapping, None, span)
        yield n, structured, registry


def one(document: str, path: Path, shell: Path, chrome: Path) -> dict[str, Any]:
    """One label's parity line (the module docstring)."""
    out: dict[str, Any] = {}
    started = time.monotonic()
    try:
        body, view = _read(path.read_bytes())
    except DocxRefusedError as refused:
        return {"outcome": "reader-refused", "code": refused.code}
    out |= {"outcome": "read", "view": view, "paragraphs": len(body.paragraphs)}
    out["read_s"] = round(time.monotonic() - started, 2)
    out["parts"] = []
    check_script = _check_script()
    answers: list[str] = []
    real_sections, real_markers = browser.browser_sections, browser.browser_markers

    def kept[T](given: T) -> T:
        answers.append(hashlib.sha256(canonical_json(given).encode("utf-8")).hexdigest())
        return given

    def sections(divs: list[str], chrome: Path = browser.CHROME) -> list[dict[str, Any]]:
        return kept(real_sections(divs, chrome))

    def markers(divs: list[str], chrome: Path = browser.CHROME) -> list[list[str]]:
        return kept(real_markers(divs, chrome))

    with tempfile.TemporaryDirectory() as folder:
        chromes = {
            "google": chrome,
            "shell": shell,
            "shell2": shell,
            "window": window_launcher(shell, Path(folder)),
        }
        browser.browser_sections, browser.browser_markers = sections, markers
        try:
            for n, structured, registry in _parts(document, body):
                entry: dict[str, Any] = {"part": n, "builds": structured["ready"]}
                out["parts"].append(entry)
                if not structured["ready"]:
                    continue
                try:
                    built = word_epi.sections(body, structured, registry)
                except word_epi.RefusedError as refused:
                    entry["documentRefused"] = refused.code
                    continue
                drawn = [s for s in built["sections"] if s["refusal"] is None and s["narrative"]]
                entry |= {
                    "sections": len(built["sections"]),
                    "drawn": len(drawn),
                    "everySection": all(s["refusal"] is None for s in built["sections"]),
                    "narrativeChars": sum(len(s["narrative"]) for s in drawn),
                }
                if not drawn:
                    continue
                verdicts: dict[str, Any] = {}
                raw: dict[str, list[str]] = {}
                for name, at in chromes.items():
                    answers.clear()
                    began = time.monotonic()
                    verdicts[name] = drawing.check(body, built, at)["sections"]
                    entry[f"check_{name}_s"] = round(time.monotonic() - began, 2)
                    raw[name] = list(answers)
                entry["agree"] = {k: sum(v["agrees"] for v in verdicts[k]) for k in verdicts}
                entry["differ"] = [[v["key"], v["where"]] for v in verdicts["shell"] if v["where"]]
                # SHELL's raw answers by digest, to compare runs on other machines.
                entry["shell_raw_sha256"] = hashlib.sha256(
                    json.dumps(raw["shell"]).encode("utf-8")
                ).hexdigest()
                for name in ("google", "shell2", "window"):
                    entry[f"verdicts_shell_eq_{name}"] = verdicts["shell"] == verdicts[name]
                    entry[f"raw_shell_eq_{name}"] = raw["shell"] == raw[name]
                divs = [s["narrative"] for s in drawn]
                for name in ("shell", "google"):
                    found = check_script.parsed_alike(divs, chromes[name])
                    entry[f"parse_{name}_same"] = found.count(None)
                    entry[f"parse_{name}_where"] = sorted({x for x in found if x})
        finally:
            browser.browser_sections, browser.browser_markers = real_sections, real_markers
    out["peak_python_mb"] = _peak_mb(resource.RUSAGE_SELF)
    out["peak_waited_child_mb"] = _peak_mb(resource.RUSAGE_CHILDREN)
    return out


def parity(document: str, folder: Path, shell: Path, chrome: Path) -> None:
    """Writes each label's parity line, each made by a process of its own."""
    for index, path in enumerate(_files(folder)):
        made = subprocess.run(
            [sys.executable, __file__, "one", document, str(path), str(shell), str(chrome)],
            capture_output=True,
            check=False,
        )
        line = json.loads(made.stdout) if made.returncode == 0 else {"outcome": "crashed"}
        entry = {"document": document, "folder": str(folder), "index": index, **line}
        sys.stdout.write(json.dumps(entry) + "\n")
        sys.stdout.flush()


def _signable(results: list[Path]) -> list[tuple[str, Path, dict[str, Any]]]:
    """Every label ``python -m zone_a.drawing`` can sign: its kind, its path and its request."""
    out: list[tuple[str, Path, dict[str, Any]]] = []
    for case in json.loads((COMMITTED / "cases.json").read_text("utf-8")):
        if "refusal" not in json.loads((COMMITTED / f"{case['name']}.json").read_bytes()):
            out.append(("committed", COMMITTED / f"{case['name']}.docx", case["request"]))
    for lines in results:
        for line in map(json.loads, lines.read_text("utf-8").splitlines()):
            for part in line.get("parts", []):
                if part.get("everySection"):
                    path = _files(Path(line["folder"]))[line["index"]]
                    asked = {
                        "document": line["document"],
                        "view": line["view"],
                        "part": part["part"],
                    }
                    versions = recompute.versions(line["document"])
                    out.append(("corpus", path, asked | {"assignments": {}, "versions": versions}))
    return out


def repeat(shell: Path, results: list[Path], runs: int) -> None:
    """Writes each signable label's repeat line (the module docstring)."""
    environment = {"LABEL_CHROME": str(shell), "PATH": "/usr/bin:/bin"}
    for index, (kind, path, asked) in enumerate(_signable(results)):
        data = path.read_bytes()
        request = {"docxSha256": hashlib.sha256(data).hexdigest(), "recompute": asked}
        raw = canonical_json(request).encode("utf-8")

        def run(_: object = None, label: Path = path, given: bytes = raw) -> tuple[Any, ...]:
            began = time.monotonic()
            with subprocess.Popen(
                [sys.executable, "-X", "utf8", "-m", "zone_a.drawing", str(label)],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=environment,
            ) as process:
                written, said = process.communicate(given)
            digest = hashlib.sha256(written).hexdigest()
            return process.returncode, digest, said.decode()[:200], time.monotonic() - began

        one_by_one = [run() for _ in range(runs)]
        with ThreadPoolExecutor(2) as pool:
            in_pairs = [r for _ in range(runs // 2) for r in pool.map(run, range(2))]
        every = one_by_one + in_pairs

        def times(rows: list[tuple[Any, ...]]) -> list[float]:
            took = [row[3] for row in rows]
            return [round(min(took), 2), round(statistics.mean(took), 2), round(max(took), 2)]

        line = {
            "label": index,
            "kind": kind,
            "document": asked["document"],
            "runs": len(every),
            "statuses": sorted({row[0] for row in every}),
            "outputs": len({row[1] for row in every}),
            "stderr": sorted({row[2] for row in every if row[2]}),
            "one_by_one_s": times(one_by_one),
            "in_pairs_s": times(in_pairs),
            "peak_waited_child_mb": _peak_mb(resource.RUSAGE_CHILDREN),
        }
        sys.stdout.write(json.dumps(line) + "\n")
        sys.stdout.flush()


def summary(files: list[Path]) -> dict[str, Any]:
    """The design note's counts from parity and repeat lines."""
    lines = [json.loads(text) for f in files for text in f.read_text("utf-8").splitlines()]
    out: dict[str, Any] = {}
    for document in ("smpc", "pl"):
        labels = [line for line in lines if line.get("document") == document and "index" in line]
        parts = [p for line in labels for p in line.get("parts", [])]
        built = [p for p in parts if p.get("builds") and "documentRefused" not in p]
        drawn = [p for p in built if p.get("drawn")]
        same = {
            k: sum(p[k] is True for p in drawn)
            for k in (
                f"{w}_shell_eq_{c}"
                for w in ("verdicts", "raw")
                for c in ("google", "shell2", "window")
            )
        }
        out[document] = {
            "files": len(labels),
            "outcomes": dict(Counter(line["outcome"] for line in labels)),
            "build": len(built),
            "readyButDocumentRefused": sum("documentRefused" in p for p in parts),
            "drawn": len(drawn),
            "everySection": sum(bool(p.get("everySection")) for p in built),
            "sections": sum(p["drawn"] for p in drawn),
            "agree": {
                k: sum(p["agree"][k] for p in drawn)
                for k in ("google", "shell", "shell2", "window")
            },
            "same": same,
            "parsedAlike": {
                k: sum(p[f"parse_{k}_same"] for p in drawn) for k in ("shell", "google")
            },
            "differ": sum(len(p["differ"]) for p in drawn),
        }
    repeats = [line for line in lines if "runs" in line]
    out["repeat"] = {
        "labels": len(repeats),
        "runs": sum(line["runs"] for line in repeats),
        "allExitZero": all(line["statuses"] == [0] for line in repeats),
        "oneOutputEach": all(line["outputs"] == 1 for line in repeats),
    }
    return out


def main(argv: list[str] | None = None) -> int:
    """Runs the command named (the module docstring).

    Returns:
        The exit status.
    """
    parser = argparse.ArgumentParser()
    commands = parser.add_subparsers(dest="command", required=True)
    one_line = commands.add_parser("one")
    one_line.add_argument("document", choices=["smpc", "pl"])
    one_line.add_argument("label", type=Path)
    one_line.add_argument("shell", type=Path)
    one_line.add_argument("chrome", type=Path)
    each = commands.add_parser("parity")
    each.add_argument("document", choices=["smpc", "pl"])
    each.add_argument("folder", type=Path)
    each.add_argument("shell", type=Path)
    each.add_argument("--chrome", type=Path, default=browser.CHROME)
    again = commands.add_parser("repeat")
    again.add_argument("shell", type=Path)
    again.add_argument("results", type=Path, nargs="*")
    again.add_argument("--runs", type=int, default=20)
    total = commands.add_parser("summary")
    total.add_argument("files", type=Path, nargs="+")
    args = parser.parse_args(argv)
    if args.command == "one":
        sys.stdout.write(json.dumps(one(args.document, args.label, args.shell, args.chrome)))
    elif args.command == "parity":
        parity(args.document, args.folder, args.shell, args.chrome)
    elif args.command == "repeat":
        repeat(args.shell, args.results, args.runs)
    else:
        sys.stdout.write(json.dumps(summary(args.files), indent=1) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
