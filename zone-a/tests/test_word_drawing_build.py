"""The drawing build's steps in the image (``scripts/word_drawing_build.py``)."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from zone_a import drawing, recompute
from zone_a.canonical_json import canonical_json

ROOT = Path(__file__).resolve().parents[2]
SHA = "a" * 64
COMMIT = "b" * 40
DIGEST = "sha256:" + "c" * 64


def _build() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "word_drawing_build", ROOT / "zone-a" / "scripts" / "word_drawing_build.py"
    )
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


build = _build()


def _asked(**changed: Any) -> dict[str, Any]:
    asked = {
        "document": "smpc",
        "view": None,
        "part": 0,
        "assignments": {},
        "versions": recompute.versions("smpc"),
    }
    return {"docxSha256": SHA, "recompute": asked | changed}


def _raw(value: Any) -> bytes:
    return canonical_json(value).encode("utf-8")


def _drawn(request: dict[str, Any], **changed: Any) -> bytes:
    fields = {
        "recordVersion": drawing.RECORD_VERSION,
        "request": request,
        "document": {"sha256": SHA, "byteLength": 10},
        "recompute": {"outputSha256": "d" * 64},
        "drawing": {"version": drawing.DRAWING_VERSION, "chrome": "Chrome 154.0.8037.57"},
        "sections": [{"key": "smpc.1", "narrativeDivSha256": "e" * 64}],
    } | changed
    return (canonical_json(fields) + "\n").encode("utf-8")


def test_a_canonical_request_names_its_docx() -> None:
    assert build.request(_raw(_asked())) == SHA


@pytest.mark.parametrize(
    "raw",
    [
        _raw(_asked()) + b"\n",  # not its canonical bytes
        json.dumps(_asked()).encode("utf-8"),  # spaced, unsorted
        b'{"docxSha256":"' + SHA.encode() + b'","docxSha256":"' + SHA.encode() + b'"}',
        _raw(_asked() | {"extra": 1}),
        _raw({"docxSha256": SHA.upper(), "recompute": _asked()["recompute"]}),
        _raw(_asked(document="spc")),  # not the recompute request's shape
        _raw(_asked(assignments={"smpc.1": "3"})),
        _raw(_asked(document=["smpc"])),
        b"[" * 100_000,
        b"\xff",
    ],
)
def test_any_other_request_is_refused(raw: bytes) -> None:
    with pytest.raises(build.RefusedError, match="request"):
        build.request(raw)


def test_two_equal_drawings_make_the_record_with_the_build_s_fields() -> None:
    asked = _asked()
    drawn = _drawn(asked)
    made = json.loads(build.record("dev", COMMIT, DIGEST, "3", _raw(asked), drawn, drawn))
    expected = json.loads(drawn) | {"environment": "dev", "commitSha": COMMIT, "keyVersion": 3}
    expected["drawing"] |= {"imageDigest": DIGEST}
    assert made == expected
    # Its canonical JSON exactly, with no line feed: the bytes that are signed.
    assert build.record("dev", COMMIT, DIGEST, "3", _raw(asked), drawn, drawn) == _raw(expected)


@pytest.mark.parametrize(
    ("environment", "commit", "digest", "version", "first", "second", "why"),
    [
        ("dev", COMMIT, DIGEST, "1", _drawn(_asked()), _drawn(_asked(), sections=[]), "differ"),
        ("test", COMMIT, DIGEST, "1", _drawn(_asked()), _drawn(_asked()), "malformed"),
        ("dev", COMMIT[:-1], DIGEST, "1", _drawn(_asked()), _drawn(_asked()), "malformed"),
        ("dev", COMMIT, "c" * 64, "1", _drawn(_asked()), _drawn(_asked()), "malformed"),
        ("dev", COMMIT, DIGEST, "01", _drawn(_asked()), _drawn(_asked()), "malformed"),
        ("dev", COMMIT, DIGEST, "1", _drawn(_asked(part=1)), _drawn(_asked(part=1)), "fields"),
        ("dev", COMMIT, DIGEST, "1", _drawn(_asked(), x=1), _drawn(_asked(), x=1), "fields"),
        ("dev", COMMIT, DIGEST, "1", b"{}", b"{}", "fields"),
        ("dev", COMMIT, DIGEST, "1", b"x", b"x", "canonical"),
    ],
)
def test_anything_else_makes_no_record(
    environment: str, commit: str, digest: str, version: str, first: bytes, second: bytes, why: str
) -> None:
    with pytest.raises(build.RefusedError, match=why):
        build.record(environment, commit, digest, version, _raw(_asked()), first, second)


def test_the_command_line_writes_the_record_or_nothing(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    asked, drawn = tmp_path / "request.json", tmp_path / "drawn.json"
    asked.write_bytes(_raw(_asked()))
    drawn.write_bytes(_drawn(_asked()))
    files = [str(asked), str(drawn), str(drawn)]
    assert build.main(["record", "dev", COMMIT, DIGEST, "1", *files]) == 0
    assert json.loads(capsys.readouterr().out)["keyVersion"] == 1
    assert build.main(["record", "dev", COMMIT, DIGEST, "1", str(asked), str(drawn), "none"]) == 1
    assert capsys.readouterr() == ("", "refused: an input cannot be read\n")
    assert build.main(["record"]) == 2
