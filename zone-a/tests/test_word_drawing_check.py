"""The word-drawing image's own checks (``scripts/word_drawing_check.py``) cannot pass on less."""

from __future__ import annotations

import copy
import importlib.util
import json
import shutil
import subprocess
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
from label_docx import browser

from zone_a import drawing, recompute
from zone_a.canonical_json import canonical_json
from zone_a.certified import Body

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "zone-a" / "scripts"


def _load(name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _hardened(root: Path) -> Path:
    """A filesystem as the hardened container shows it; returns the checkout's mount point."""
    (root / "proc" / "net").mkdir(parents=True)
    (root / "proc" / "self").mkdir()
    (root / "proc" / "net" / "route").write_text("Iface\tDestination\n", "utf-8")
    (root / "proc" / "net" / "ipv6_route").write_text("0" * 32 + " 80 lo\n", "utf-8")
    (root / "proc" / "self" / "status").write_text(
        "CapBnd:\t0000000000000000\nNoNewPrivs:\t1\n", "utf-8"
    )
    checkout = root / "work"
    (root / "proc" / "mounts").write_text(
        f"overlay / overlay ro,relatime 0 0\n"
        f"/dev/root {checkout}/zone-a/src ext4 ro,relatime 0 0\n"
        f"/dev/root {checkout}/qrd/registry ext4 ro,relatime 0 0\n"
        "tmpfs /tmp tmpfs rw,nosuid,nodev,noexec 0 0\n",
        "utf-8",
    )
    cgroup = root / "sys" / "fs" / "cgroup"
    cgroup.mkdir(parents=True)
    (cgroup / "memory.max").write_text(f"{6 << 30}\n", "utf-8")
    (cgroup / "pids.max").write_text("2048\n", "utf-8")
    (root / "tmp").mkdir()
    for read_only in (
        checkout / "zone-a" / "src",
        root / "home" / "node",
        root / "opt" / "renderer",
    ):
        read_only.mkdir(parents=True)
        read_only.chmod(0o555)
    return checkout


@pytest.mark.parametrize(
    ("weaken", "found"),
    [
        (lambda r: (r / "proc/net/route").write_text("h\neth0\t0\n", "utf-8"), "a route"),
        (lambda r: (r / "home/node").chmod(0o755), "home/node is writable"),
        (lambda r: (r / "work/zone-a/src").chmod(0o755), "zone-a/src is writable"),
        (lambda r: (r / "opt/renderer").chmod(0o755), "opt/renderer is writable"),
        (
            lambda r: (r / "proc/mounts").write_text(
                (r / "proc/mounts")
                .read_text("utf-8")
                .replace("registry ext4 ro,", "registry ext4 rw,"),
                "utf-8",
            ),
            "registry is mounted writable",
        ),
        (
            lambda r: (r / "proc/mounts").write_text(
                (r / "proc/mounts").read_text("utf-8").replace("/zone-a/src ", "/zone-a/other "),
                "utf-8",
            ),
            "sources are not mounted",
        ),
        (
            lambda r: (r / "proc/mounts").write_text(
                (r / "proc/mounts")
                .read_text("utf-8")
                .replace("tmpfs /tmp tmpfs", "/dev/x /tmp ext4"),
                "utf-8",
            ),
            "/tmp is not a tmpfs",
        ),
        (lambda r: (r / "tmp").chmod(0o555), "/tmp is not writable"),
        (lambda r: (r / "sys/fs/cgroup/memory.max").write_text("max\n", "utf-8"), "memory"),
        (lambda r: (r / "sys/fs/cgroup/memory.max").write_text(f"{7 << 30}\n", "utf-8"), "memory"),
        (lambda r: (r / "sys/fs/cgroup/pids.max").unlink(), "processes"),
        (
            lambda r: (r / "proc/self/status").write_text("CapBnd:\t00000000a80425fb\n", "utf-8"),
            "capabilities",
        ),
    ],
)
def test_the_isolation_probe_finds_each_weakening(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, weaken: Any, found: str
) -> None:
    script = _load("word_drawing_check")
    monkeypatch.setattr(script, "_reachable", lambda _host: False)
    checkout = _hardened(tmp_path)
    try:
        assert script.isolation(tmp_path, checkout) == []
        weaken(tmp_path)
        assert any(found in finding for finding in script.isolation(tmp_path, checkout))
    finally:
        for path in tmp_path.rglob("*"):
            if path.is_dir():
                path.chmod(0o755)
    monkeypatch.setattr(script, "_reachable", lambda host: host == "192.0.2.1")
    assert script.isolation(tmp_path, checkout)[0] == "192.0.2.1 is reachable"


def _honest(label: Path, raw: bytes) -> tuple[int, bytes, bytes]:
    """python -m zone_a.drawing, in this process, with every drawn section agreeing."""
    try:
        fields = drawing.record(label.read_bytes(), json.loads(raw))
    except recompute.RefusedError as refused:
        return 1, b"", f"refused: {refused.code}\n".encode()
    return 0, (canonical_json(fields) + "\n").encode(), b""


def _agreeing(monkeypatch: pytest.MonkeyPatch) -> None:
    def check(_body: Body, built: dict[str, Any], _chrome: Path) -> dict[str, Any]:
        keys = [s["key"] for s in built["sections"] if s["refusal"] is None and s["narrative"]]
        sections = [{"key": key, "agrees": True, "where": None} for key in keys]
        return {"checker": drawing.DRAWING_VERSION, "application": "Chrome 1", "sections": sections}

    monkeypatch.setattr(drawing, "check", check)
    monkeypatch.setattr(browser, "chrome_version", lambda _chrome=None: "Chrome 1")


def test_the_committed_labels_pass_only_as_committed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    script = _load("word_drawing_check")
    _agreeing(monkeypatch)
    failures, divs = script.certified_labels(_honest)
    assert failures == []
    assert len(divs) >= 100
    # A crash where a refusal was committed is no refusal: only its own closed code is.
    for said in (b"error: RecursionError\n", b"refused: request\n", b""):

        def crashing(label: Path, raw: bytes, said: bytes = said) -> tuple[int, bytes, bytes]:
            return (1, b"", said) if label.stem == "smpc-refused" else _honest(label, raw)

        (failure,) = script.certified_labels(crashing)[0]
        assert failure.startswith("smpc-refused: not refused with 'refused: section' alone")
    # Fewer labels than committed fail, whatever they draw.
    fewer = tmp_path / "fewer"
    shutil.copytree(script.CERTIFIED, fewer)
    cases = json.loads((fewer / "cases.json").read_text("utf-8"))
    (fewer / "cases.json").write_text(json.dumps(cases[:2]), "utf-8")
    assert script.certified_labels(_honest, fewer)[0] == [
        "2 labels to sign and 0 to refuse, fewer than committed"
    ]


def test_the_word_made_labels_must_answer_as_recorded(monkeypatch: pytest.MonkeyPatch) -> None:
    script = _load("word_drawing_check")
    fixtures = _load("word_fixtures")
    recorded = json.loads((fixtures.FIXTURES / "chrome.json").read_text("utf-8"))
    by_digest = {doc["narratives"]: doc for doc in recorded["documents"].values()}

    def replay(answers: dict[str, Any]) -> None:
        monkeypatch.setattr(
            browser,
            "browser_sections",
            lambda divs, _chrome=None: answers[fixtures.digest(divs)]["shown"],
        )
        monkeypatch.setattr(
            browser,
            "browser_markers",
            lambda divs, _chrome=None: answers[fixtures.digest(divs)]["markers"],
        )

    monkeypatch.setattr(browser, "chrome_version", lambda _chrome=None: recorded["application"])
    replay(by_digest)
    failures, divs = script.word_smpcs()
    assert failures == []
    assert len(divs) >= 140
    # A raw answer the verdict ignores, but not the recording: it fails all the same.
    changed = copy.deepcopy(by_digest)
    first = next(iter(changed.values()))
    first["shown"][0]["extra"] = 1
    replay(changed)
    (failure,) = script.word_smpcs()[0]
    assert failure.endswith(f"raw answers not {recorded['application']}'s recording")


def test_too_few_narratives_fail_the_parse_check() -> None:
    script = _load("word_drawing_check")
    assert script.parse_failures([None] * script.MIN_NARRATIVES) == []
    assert script.parse_failures([None, "/0/1"] * script.MIN_NARRATIVES) == [
        f"narrative {2 * i + 2}: HTML and XML differ at /0/1" for i in range(script.MIN_NARRATIVES)
    ]
    assert script.parse_failures([None]) == ["1 narratives parsed, fewer than committed"]


# What the image runs: the entry point, and the checks' scripts (loaded by path, as they load).
_IMPORTED = """
import importlib.util, json, pathlib, sys
before = set(sys.modules)
import zone_a.drawing
for name in ("word_drawing_check", "word_fixtures"):
    spec = importlib.util.spec_from_file_location(name, pathlib.Path(sys.argv[1]) / f"{name}.py")
    spec.loader.exec_module(importlib.util.module_from_spec(spec))
sys.stdout.write(json.dumps(sorted({m.split(".")[0] for m in set(sys.modules) - before})))
"""


def test_the_drawing_imports_nothing_the_image_lacks() -> None:
    """The image has Python alone, and zone_a and label_docx mounted: no pydantic, nothing else."""
    with subprocess.Popen(
        [sys.executable, "-I", "-c", _IMPORTED, str(SCRIPTS)], stdout=subprocess.PIPE, env={}
    ) as process:
        loaded, _ = process.communicate()
    assert set(json.loads(loaded)) - set(sys.stdlib_module_names) == {"zone_a", "label_docx"}
