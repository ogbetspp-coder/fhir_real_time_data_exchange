"""Damaged documents: never an error, never a read the conservation check cannot account for.

Seeded damage to real documents, as a disk, a network or an editor could do it: bytes of a part
changed inside an intact zip (well-formedness, names, values), and bytes of the file changed or
cut off. Every outcome must be a refusal with a code or a certified read, and the reader and the
independent check must agree: none refused as ``uncertified``.
"""

from __future__ import annotations

import io
import json
import random
import time
import tracemalloc
import zipfile
from collections.abc import Callable
from pathlib import Path

import pytest

from label_docx import documents, reader
from label_docx.epi import EpiRefusedError, read_epi
from label_docx.epi import _html_otherwise as epi_html_otherwise

CORPUS = Path(__file__).resolve().parents[1] / "corpus"
DOCUMENTS = [
    CORPUS / "ema-qrd" / "qrd-product-information-template-version-104_en.docx",
    CORPUS / "numbering-cases" / "notes-mixed.docx",
    CORPUS / "word-authored" / "table-of-contents.docx",
    CORPUS / "ema-epi" / "jentadueto-smpc-en.json",
]
_TRIES = 300


def _damage_a_part(data: bytes, rng: random.Random) -> bytes:
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        parts = {name: archive.read(name) for name in archive.namelist()}
    name = rng.choice(sorted(n for n in parts if n.endswith((".xml", ".rels"))))
    damaged = bytearray(parts[name])
    for _ in range(rng.randint(1, 3)):
        damaged[rng.randrange(len(damaged))] = rng.choice(b"<>/=\"' azAZ09&;\x00\xff")
    parts[name] = bytes(damaged)
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as archive:
        for part, content in parts.items():
            archive.writestr(part, content)
    return out.getvalue()


def _damage_the_file(data: bytes, rng: random.Random) -> bytes:
    damaged = bytearray(data)
    for _ in range(rng.randint(1, 4)):
        damaged[rng.randrange(len(damaged))] = rng.randrange(256)
    if rng.random() < 0.2:
        damaged = damaged[: rng.randrange(len(damaged))]
    return bytes(damaged)


@pytest.mark.parametrize("path", DOCUMENTS, ids=[p.name for p in DOCUMENTS])
def test_damage_is_refused_or_read_and_certified_never_an_error(path: Path) -> None:
    data = path.read_bytes()
    rng = random.Random(path.name)
    outcomes: dict[str, int] = {}
    for attempt in range(_TRIES):
        if path.suffix == ".docx" and attempt % 2 == 0:
            damaged = _damage_a_part(data, rng)
        else:
            damaged = _damage_the_file(data, rng)
        result, read = documents.kind(damaged).read(damaged)
        value = json.loads(result)
        outcome = "read" if read else value["refusal"]["code"]
        if read:
            assert value["certificate"]["output"]["characters"] >= 0
        outcomes[outcome] = outcomes.get(outcome, 0) + 1
    assert "uncertified" not in outcomes, outcomes
    assert sum(outcomes.values()) == _TRIES


def test_a_compressed_xml_bomb_is_refused_before_it_is_built(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A part the reader does not read, 4 MB of "<b/>" deflated to a few kilobytes: refused as
    # the elements are counted, not after a tree of a million elements is built.
    monkeypatch.setattr(reader, "MAX_ELEMENTS", 10_000)
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as archive:
        with zipfile.ZipFile(CORPUS / "word-authored" / "table-of-contents.docx") as source:
            for info in source.infolist():
                archive.writestr(info.filename, source.read(info))
        archive.writestr("customXml/item9.xml", "<x>" + "<b/>" * 1_000_000 + "</x>")
    data = out.getvalue()
    assert len(data) < 100_000
    tracemalloc.start()
    try:
        result, read = documents.kind(data).read(data)
        peak = tracemalloc.get_traced_memory()[1]
    finally:
        tracemalloc.stop()
    assert not read
    assert json.loads(result)["refusal"] == {
        "code": "invalid-package",
        "detail": "parts over 10000 XML elements",
    }
    assert peak < 32 * 1024 * 1024, peak


def _seconds(read: Callable[[], object]) -> float:
    started = time.perf_counter()
    read()
    return time.perf_counter() - started


def test_a_bundle_built_to_make_a_pattern_backtrack_is_refused_at_once() -> None:
    # A string never closed: from each quote in turn, the depth check's pattern once scanned to
    # the end (quadratic: 32 KB took seconds, 1 MB hours). Not JSON either way.
    attack = b'{"' + b'\\"' * 512_000
    with pytest.raises(EpiRefusedError) as refused:
        read_epi(attack)
    assert refused.value.code == "invalid-bundle"
    assert _seconds(lambda: documents.kind(attack).read(attack)) < 5


def test_a_section_built_to_rescan_its_attributes_is_read_at_once() -> None:
    # Each "<a" in an attribute value once re-scanned every attribute after it for "/>".
    div = '<div xmlns="http://www.w3.org/1999/xhtml"><p' + ' x="&lt;a"' * 50_000 + ">t</p></div>"
    assert _seconds(lambda: epi_html_otherwise(div.replace("&lt;", "<"))) < 5
