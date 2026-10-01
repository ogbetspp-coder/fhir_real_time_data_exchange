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
import zipfile
from pathlib import Path

import pytest

from label_docx import documents

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
