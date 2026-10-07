"""The word-drawing image's checks, run inside it (docs/design/certified-word-drawing.md, PR 1).

    node scripts/render/word-drawing.mjs /work/zone-a/scripts/word_drawing_check.py

CI's Word drawing job runs this in the image, hardened (``scripts/render/word-drawing.mjs``):

1. the container is as hardened as the drawing needs: no network, a read-only root and checkout,
   no capabilities, no new privileges, not root; and Chrome starts in it, through the image's
   launcher, on an empty page (where it does not, Chrome's own last words are printed, and nothing
   else is checked);
2. each committed certified Word label (``test/fixtures/certified-word/recompute``):
   ``python -m zone_a.drawing``, run twice in two processes, writes the same bytes, and they are
   the fields the label, its request and its committed recompute result give; the label the
   recompute refuses is refused, with nothing written;
3. each Word-made SmPC and the QRD template (``tests/fixtures/word-smpc``): every carried section
   agrees, as in Google Chrome's recording (``chrome.json``), whose raw answers are compared too
   and counted;
4. every narrative drawn above: Chrome's HTML parser and its XML parser make the same tree
   (``parsed_alike``).

It prints counts, versions and times, never the text, and exits 1 on any failure.
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


def isolation() -> list[str]:
    """What is not hardened in this container (empty where all is), as the renderer's smoke."""
    found: list[str] = []
    for host in ("192.0.2.1", "2001:db8::1"):  # documentation addresses: nothing answers
        try:
            socket.create_connection((host, 443), timeout=3).close()
            found.append(f"{host} is reachable")
        except OSError:
            pass
    routes = Path("/proc/net/route").read_text("utf-8").strip().splitlines()[1:]
    six = Path("/proc/net/ipv6_route")
    routes6 = [
        line for line in (six.read_text("utf-8").splitlines() if six.exists() else []) if line
    ]
    if routes or any(line.split()[-1] != "lo" for line in routes6):
        found.append("the container has a route")
    for where in (ROOT / ".word-drawing-probe", Path("/home/node/.word-drawing-probe")):
        try:
            where.write_bytes(b"")
            found.append(f"{where} is writable")
        except OSError:
            pass
    status = dict(
        line.split(":\t", 1)
        for line in Path("/proc/self/status").read_text("utf-8").splitlines()
        if ":\t" in line
    )
    if status.get("CapBnd", "").strip() != "0000000000000000":
        found.append("capabilities are bounded otherwise than none")
    if status.get("NoNewPrivs", "").strip() != "1":
        found.append("new privileges are allowed")
    if os.getuid() == 0:
        found.append("running as root")
    return found


def certified_labels() -> tuple[list[str], list[str]]:
    """Step 2 (the module docstring): the failures, and the narratives drawn."""
    failures: list[str] = []
    divs: list[str] = []
    cases = json.loads((CERTIFIED / "cases.json").read_text("utf-8"))
    for case in cases:
        name = case["name"]
        label = CERTIFIED / f"{name}.docx"
        data = label.read_bytes()
        request = {"docxSha256": _sha256(data), "recompute": case["request"]}
        started = time.monotonic()
        runs = [
            subprocess.run(
                [sys.executable, "-m", "zone_a.drawing", str(label)],
                input=canonical_json(request).encode("utf-8"),
                capture_output=True,
                check=False,
            )
            for _ in range(2)
        ]
        took = (time.monotonic() - started) / 2
        committed = (CERTIFIED / f"{name}.json").read_bytes()
        result = json.loads(committed)
        if "refusal" in result:
            if any(r.returncode != 1 or r.stdout for r in runs):
                failures.append(f"{name}: not refused with nothing written")
            sys.stdout.write(f"{name}: refused, as committed ({took:.1f} s a run)\n")
            continue
        if runs[0].returncode != 0:
            failures.append(f"{name}: {runs[0].stderr.decode('utf-8', 'replace').strip()}")
            continue
        if runs[1].returncode != 0 or runs[0].stdout != runs[1].stdout:
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
        if runs[0].stdout != (canonical_json(expected) + "\n").encode("utf-8"):
            failures.append(f"{name}: the fields are not the label's and its recompute's")
        divs += [s["narrative"] for s in drawn]
        sys.stdout.write(f"{name}: {len(drawn)} sections drawn, twice alike ({took:.1f} s a run)\n")
    return failures, divs


def word_smpcs() -> tuple[list[str], list[str]]:
    """Step 3 (the module docstring): the failures, and the narratives drawn."""
    fixtures = _word_fixtures()
    recorded = json.loads((fixtures.FIXTURES / "chrome.json").read_text("utf-8"))
    registry = json.loads(fixtures.REGISTRY.read_text("utf-8"))
    mapping = json.loads(fixtures.MAPPING.read_text("utf-8"))
    failures: list[str] = []
    divs: list[str] = []
    raw_alike = 0
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
        raw_alike += (
            browser.browser_sections(mine) == theirs["shown"]
            and browser.browser_markers(mine) == theirs["markers"]
        )
        divs += mine
        sys.stdout.write(f"{path.stem}: {len(verdicts)} sections agree ({took:.1f} s)\n")
    sys.stdout.write(
        f"raw answers equal to {recorded['application']}'s recording: {raw_alike} of {len(paths)}\n"
    )
    return failures, divs


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
    failures += [
        f"narrative {i + 1}: HTML and XML differ at {at}" for i, at in enumerate(parsed) if at
    ]
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
