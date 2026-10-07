"""The word-drawing image's checks, run inside it (docs/design/certified-word-drawing.md, PR 1).

    node scripts/render/word-drawing.mjs /work/zone-a/scripts/word_drawing_check.py

CI's Word drawing job runs this in the image, hardened (``scripts/render/word-drawing.mjs``):

1. the container is hardened as that launcher makes it, read back from inside (``isolation``): no
   network or route; the root, the image's directories and every mount of the checkout read-only,
   the checkout's sources among them; ``/tmp`` a writable tmpfs; memory bounded to 6 GiB and
   processes to 2,048 (cgroup v2, else v1); no capabilities, no new privileges, not root. And
   Chrome starts, through the image's launcher, on an empty page (where it does not, Chrome's own
   last words are printed, and nothing else is checked);
2. each committed certified Word label (``test/fixtures/certified-word/recompute``):
   ``python -m zone_a.drawing``, run twice in two processes, writes the same bytes, and they are
   the fields the label, its request and its committed recompute result give; a label the
   recompute refuses is refused with nothing written and exactly its committed code on standard
   error;
3. each Word-made SmPC and the QRD template (``tests/fixtures/word-smpc``): every carried section
   agrees, and Chrome's raw answers are byte for byte Google Chrome's recording (``chrome.json``);
4. every narrative drawn above: Chrome's HTML parser and its XML parser make the same tree
   (``parsed_alike``).

Each step also requires at least the fixtures committed on 2026-10-07 (``MIN_*``), so no check
passes on fewer. It prints counts, versions and times, never the text, and exits 1 on any failure.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable
from pathlib import Path
from types import ModuleType
from typing import Final

from label_docx import browser

from zone_a import drawing, word_epi
from zone_a.canonical_json import canonical_json, sha256_utf8
from zone_a.certified import read_body
from zone_a.structure import structure

ROOT: Final = Path(__file__).resolve().parents[2]
CERTIFIED: Final = ROOT / "test" / "fixtures" / "certified-word" / "recompute"
# The fixtures committed on 2026-10-07: 4 labels signed and 1 refused, 6 Word-made labels, 262
# narratives. Fewer means fixtures went missing; a change that means fewer lowers these on purpose.
MIN_SIGNED: Final = 4
MIN_REFUSED: Final = 1
MIN_WORD_MADE: Final = 6
MIN_NARRATIVES: Final = 250
# What scripts/render/run.mjs's HARDENING bounds the container to.
MEMORY_LIMIT: Final = 6 << 30
PROCESS_LIMIT: Final = 2048

# What ``python -m zone_a.drawing`` did: its status, its standard output and its standard error.
Drawn = tuple[int, bytes, bytes]

# Each div parsed by Chrome's HTML parser (as the drawing page parses it: into an element of a
# no-quirks HTML document) and by its XML parser, as XHTML; each tree is its elements (namespace,
# name and attributes, by qualified name, in order), text and other nodes (by type), with every
# tbody replaced by its children, since the HTML parser inserts one where XHTML has none. Gives
# null for each div whose trees are the same, else the first place they differ (child indices).
_PARSED_ALIKE: Final = """
(() => {
  const divs = %s;
  const XHTML = "http://www.w3.org/1999/xhtml";
  const tree = (node) => {
    if (node.nodeType === Node.TEXT_NODE) return node.data;
    if (node.nodeType !== Node.ELEMENT_NODE) return ["#" + node.nodeType];
    const children = [];
    for (const child of node.childNodes) {
      if (child.nodeType === Node.ELEMENT_NODE && child.namespaceURI === XHTML &&
          child.localName === "tbody") {
        for (const inner of child.childNodes) children.push(tree(inner));
      } else {
        children.push(tree(child));
      }
    }
    const attributes = [...node.attributes].map((a) => [a.name, a.value]);
    return [node.namespaceURI, node.localName, attributes, children];
  };
  const where = (a, b, at) => {
    if (JSON.stringify(a) === JSON.stringify(b)) return null;
    if (!Array.isArray(a) || !Array.isArray(b) || a.length !== 4 || b.length !== 4 ||
        JSON.stringify(a.slice(0, 3)) !== JSON.stringify(b.slice(0, 3)) ||
        a[3].length !== b[3].length) return at || "the root";
    for (let i = 0; i < a[3].length; i++) {
      const found = where(a[3][i], b[3][i], at + "/" + i);
      if (found !== null) return found;
    }
    return at || "the root";
  };
  const page = document.implementation.createHTMLDocument("");
  return divs.map((div) => {
    const host = page.createElement("div");
    host.innerHTML = div;
    const xml = new DOMParser().parseFromString(div, "application/xhtml+xml");
    if (xml.getElementsByTagName("parsererror").length > 0) return "not well-formed XML";
    if (host.childNodes.length !== 1) return "the root";
    return where(tree(host.firstChild), tree(xml.documentElement), "");
  });
})()
"""


def parsed_alike(divs: list[str], chrome: Path = browser.CHROME) -> list[str | None]:
    """For each div, None where Chrome's HTML and XML parsers make the same tree, else where not.

    Raises:
        browser.BrowserError: Chrome could not be asked.
    """
    if not divs:
        return []
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as folder:
        tools = browser._DevTools(chrome, folder)
        try:
            target = tools.call("Target.createTarget", {"url": "about:blank"})["targetId"]
            tools.session = tools.call(
                "Target.attachToTarget", {"targetId": target, "flatten": True}
            )["sessionId"]
            answer = tools.call(
                "Runtime.evaluate",
                {"expression": _PARSED_ALIKE % json.dumps(divs), "returnByValue": True},
            )
        finally:
            tools.close()
    if "exceptionDetails" in answer:
        raise browser.BrowserError("the parse page failed")
    found: list[str | None] = answer["result"]["value"]
    if len(found) != len(divs):
        raise browser.BrowserError("the parse page answered for other divs")
    return found


def _word_fixtures() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "word_fixtures", Path(__file__).with_name("word_fixtures.py")
    )
    if spec is None or spec.loader is None:
        raise SystemExit("scripts/word_fixtures.py is missing")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _reachable(host: str) -> bool:
    """Whether a connection to the host's port 443 opens."""
    try:
        socket.create_connection((host, 443), timeout=3).close()
    except OSError:
        return False
    return True


def _writable(where: Path) -> bool:
    try:
        where.write_bytes(b"")
    except OSError:
        return False
    where.unlink()
    return True


def _limit(root: Path, *names: str) -> int | None:
    """A cgroup limit (v2's file, else v1's) as a number; None where it is unbounded or absent."""
    for name in names:
        at = root / "sys" / "fs" / "cgroup" / name
        if at.exists():
            value = at.read_text("utf-8").strip()
            return int(value) if value.isdigit() else None
    return None


def isolation(root: Path = Path("/"), checkout: Path = ROOT) -> list[str]:
    """What is not hardened here, empty where all is (step 1 of the module docstring).

    ``root`` is where the kernel's files and the image's directories are read, ``checkout`` where
    the checkout is mounted.
    """
    # Documentation addresses (RFC 5737, RFC 3849): nothing answers, but a route would let it out.
    found = [f"{host} is reachable" for host in ("192.0.2.1", "2001:db8::1") if _reachable(host)]
    proc = root / "proc"
    routes = (proc / "net" / "route").read_text("utf-8").strip().splitlines()[1:]
    six = proc / "net" / "ipv6_route"
    routes6 = [
        line for line in (six.read_text("utf-8").splitlines() if six.exists() else []) if line
    ]
    if routes or any(line.split()[-1] != "lo" for line in routes6):
        found.append("the container has a route")
    mounts = [line.split() for line in (proc / "mounts").read_text("utf-8").splitlines()]
    under = {m[1]: m[3].split(",") for m in mounts if m[1].startswith(f"{checkout}/")}
    if f"{checkout}/zone-a/src" not in under:
        found.append("the checkout's sources are not mounted")
    found += [
        f"{point} is mounted writable" for point, options in under.items() if "ro" not in options
    ]
    if [m[2] for m in mounts if m[1] == "/tmp"][-1:] != ["tmpfs"]:
        found.append("/tmp is not a tmpfs")
    if not _writable(root / "tmp" / ".word-drawing-probe"):
        found.append("/tmp is not writable")
    for where in (checkout / "zone-a" / "src", root / "home" / "node", root / "opt" / "renderer"):
        if _writable(where / ".word-drawing-probe"):
            found.append(f"{where} is writable")
    status = dict(
        line.split(":\t", 1)
        for line in (proc / "self" / "status").read_text("utf-8").splitlines()
        if ":\t" in line
    )
    if status.get("CapBnd", "").strip() != "0000000000000000":
        found.append("capabilities are bounded otherwise than none")
    if status.get("NoNewPrivs", "").strip() != "1":
        found.append("new privileges are allowed")
    memory = _limit(root, "memory.max", "memory/memory.limit_in_bytes")
    if memory is None or memory > MEMORY_LIMIT:
        found.append("memory is not bounded to 6 GiB")
    processes = _limit(root, "pids.max", "pids/pids.max")
    if processes is None or processes > PROCESS_LIMIT:
        found.append("processes are not bounded to 2,048")
    if os.getuid() == 0:
        found.append("running as root")
    return found


def _draw(label: Path, request: bytes) -> Drawn:
    with subprocess.Popen(
        [sys.executable, "-m", "zone_a.drawing", str(label)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    ) as process:
        written, said = process.communicate(request)
    return process.returncode, written, said


def certified_labels(
    draw: Callable[[Path, bytes], Drawn] = _draw, fixtures: Path = CERTIFIED
) -> tuple[list[str], list[str]]:
    """Step 2 (the module docstring): the failures, and the narratives drawn."""
    failures: list[str] = []
    divs: list[str] = []
    cases = json.loads((fixtures / "cases.json").read_text("utf-8"))
    signed = refused = 0
    for case in cases:
        name = case["name"]
        label = fixtures / f"{name}.docx"
        data = label.read_bytes()
        request = {"docxSha256": _sha256(data), "recompute": case["request"]}
        started = time.monotonic()
        runs = [draw(label, canonical_json(request).encode("utf-8")) for _ in range(2)]
        took = (time.monotonic() - started) / 2
        committed = (fixtures / f"{name}.json").read_bytes()
        result = json.loads(committed)
        if "refusal" in result:
            refused += 1
            # Its own closed code exactly: a crash, or another refusal, is no refusal as committed.
            said = f"refused: {result['refusal']['code']}\n".encode()
            if any(run != (1, b"", said) for run in runs):
                failures.append(f"{name}: not refused with {said.decode().strip()!r} alone")
            sys.stdout.write(f"{name}: refused, as committed ({took:.1f} s a run)\n")
            continue
        signed += 1
        if runs[0][0] != 0:
            failures.append(f"{name}: {runs[0][2].decode('utf-8', 'replace').strip()}")
            continue
        if runs[1] != runs[0]:
            failures.append(f"{name}: two runs wrote different bytes")
            continue
        drawn = [s for s in result["sections"] if s["narrative"]]
        expected = {
            "recordVersion": drawing.RECORD_VERSION,
            "request": request,
            "document": {"sha256": _sha256(data), "byteLength": len(data)},
            "recompute": {"outputSha256": _sha256(committed)},
            "drawing": {"version": drawing.DRAWING_VERSION, "chrome": browser.chrome_version()},
            "sections": [
                {"key": s["key"], "narrativeDivSha256": sha256_utf8(s["narrative"])} for s in drawn
            ],
        }
        if runs[0][1] != (canonical_json(expected) + "\n").encode("utf-8"):
            failures.append(f"{name}: the fields are not the label's and its recompute's")
        divs += [s["narrative"] for s in drawn]
        sys.stdout.write(f"{name}: {len(drawn)} sections drawn, twice alike ({took:.1f} s a run)\n")
    if signed < MIN_SIGNED or refused < MIN_REFUSED:
        failures.append(f"{signed} labels to sign and {refused} to refuse, fewer than committed")
    return failures, divs


def word_smpcs() -> tuple[list[str], list[str]]:
    """Step 3 (the module docstring): the failures, and the narratives drawn."""
    fixtures = _word_fixtures()
    recorded = json.loads((fixtures.FIXTURES / "chrome.json").read_text("utf-8"))
    registry = json.loads(fixtures.REGISTRY.read_text("utf-8"))
    mapping = json.loads(fixtures.MAPPING.read_text("utf-8"))
    failures: list[str] = []
    divs: list[str] = []
    paths = [*sorted(fixtures.FIXTURES.glob("*.docx")), fixtures.TEMPLATE]
    for path in paths:
        body = read_body(path.read_bytes())
        structured = structure(body.paragraphs, registry, mapping, fixtures.ASSIGNED.get(path.stem))
        built = word_epi.sections(body, structured, registry)
        started = time.monotonic()
        verdicts = drawing.check(body, built)["sections"]
        took = time.monotonic() - started
        if not verdicts or not all(v["agrees"] for v in verdicts):
            differ = [f"{v['key']}: {v['where']}" for v in verdicts if not v["agrees"]]
            failures.append(f"{path.stem}: {'; '.join(differ) or 'nothing drawn'}")
        mine = [
            s["narrative"] for s in built["sections"] if s["refusal"] is None and s["narrative"]
        ]
        theirs = recorded["documents"][path.stem]
        if (browser.browser_sections(mine), browser.browser_markers(mine)) != (
            theirs["shown"],
            theirs["markers"],
        ):
            failures.append(f"{path.stem}: raw answers not {recorded['application']}'s recording")
        divs += mine
        sys.stdout.write(
            f"{path.stem}: {len(verdicts)} sections agree, raw answers compared ({took:.1f} s)\n"
        )
    if len(paths) < MIN_WORD_MADE:
        failures.append(f"{len(paths)} Word-made labels, fewer than committed")
    return failures, divs


def parse_failures(parsed: list[str | None]) -> list[str]:
    """Step 4's failures: each narrative whose trees differ, and too few narratives."""
    found = [f"narrative {i + 1}: HTML and XML differ at {at}" for i, at in enumerate(parsed) if at]
    if len(parsed) < MIN_NARRATIVES:
        found.append(f"{len(parsed)} narratives parsed, fewer than committed")
    return found


def main() -> int:
    """Runs the checks (the module docstring).

    Returns:
        The exit status: 0 when every check passed.
    """
    sys.stdout.write(f"{browser.chrome_version()}; Python {sys.version.split()[0]}\n")
    failures = isolation()
    try:
        parsed_alike(["<div></div>"])
    except browser.BrowserError as error:
        return _report([*failures, f"Chrome does not start here: {error}"])
    found, certified = certified_labels()
    failures += found
    found, smpcs = word_smpcs()
    failures += found
    divs = certified + smpcs
    started = time.monotonic()
    parsed = parsed_alike(divs)
    failures += parse_failures(parsed)
    sys.stdout.write(
        f"HTML and XML parse trees: {parsed.count(None)} of {len(divs)} narratives the same"
        f" ({time.monotonic() - started:.1f} s)\n"
    )
    peak = Path("/sys/fs/cgroup/memory.peak")  # the container's, under cgroup v2
    if peak.exists():
        sys.stdout.write(f"the container's peak memory: {int(peak.read_text()) >> 20} MiB\n")
    return _report(failures)


def _report(failures: list[str]) -> int:
    for failure in failures:
        sys.stdout.write(f"FAILED {failure}\n")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
