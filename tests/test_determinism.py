"""The same document gives the same bytes: in any process, and however its parts are zipped."""

from __future__ import annotations

import io
import os
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

from label_docx.output import canonical, paragraphs, read
from label_docx.reader import DocxRefusedError, read_docx

CORPUS = sorted((Path(__file__).resolve().parents[1] / "corpus").glob("*/*.docx"))
ENVIRONMENTS = [
    ("0", "C"),
    ("1", "en_US.UTF-8"),
    ("4242", "tr_TR.UTF-8"),  # Turkish case mapping, the usual trap for .lower() and .upper()
    ("random", "C.UTF-8"),
]


@pytest.mark.parametrize("path", CORPUS, ids=lambda path: path.stem[:48])
def test_fresh_processes_under_other_hash_seeds_and_locales_write_the_same_bytes(
    path: Path,
) -> None:
    # The four processes run at once, each in its own environment.
    running = [
        subprocess.Popen(
            [sys.executable, "-m", "label_docx", str(path)],
            env={**os.environ, "PYTHONHASHSEED": seed, "LC_ALL": locale, "LANG": locale},
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        for seed, locale in ENVIRONMENTS
    ]
    outputs = set()
    for process in running:
        stdout, stderr = process.communicate()
        # Read or refused (corpus/numbering-cases holds one refusal), never an error.
        assert process.returncode in (0, 2), stderr.decode()
        outputs.add(stdout)
    assert outputs == {read(path.read_bytes())[0]}


type Date = tuple[int, int, int, int, int, int]


def _result(data: bytes) -> bytes | str:
    try:
        return canonical(paragraphs(read_docx(data)))
    except DocxRefusedError as refused:
        return refused.code


def _repack(data: bytes, reverse: bool, compression: int, date: Date) -> bytes:
    with zipfile.ZipFile(io.BytesIO(data)) as source:
        parts = [(info.filename, source.read(info)) for info in source.infolist()]
    if reverse:
        parts.reverse()
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as target:
        for name, content in parts:
            info = zipfile.ZipInfo(name, date_time=date)
            info.compress_type = compression
            target.writestr(info, content)
    return buffer.getvalue()


@pytest.mark.parametrize("path", CORPUS, ids=lambda path: path.stem[:48])
def test_the_result_depends_on_the_parts_not_on_how_they_are_zipped(path: Path) -> None:
    data = path.read_bytes()
    expected = _result(data)
    cases: list[tuple[bool, int, Date]] = [
        (True, zipfile.ZIP_STORED, (1980, 1, 1, 0, 0, 0)),
        (False, zipfile.ZIP_DEFLATED, (2099, 12, 31, 23, 59, 58)),
        (True, zipfile.ZIP_DEFLATED, (2026, 9, 30, 12, 0, 0)),
    ]
    for reverse, compression, date in cases:
        repacked = _repack(data, reverse, compression, date)
        assert repacked != data
        assert _result(repacked) == expected
