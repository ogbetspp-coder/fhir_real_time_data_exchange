"""scripts/pin_label.py writes the label lock; here against a copy, with the network stubbed."""

from __future__ import annotations

import datetime
import importlib.util
import json
import shutil
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[2]
LABELS = ROOT / "labels" / "ema-epi"


def _script() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "pin_label", ROOT / "zone-a" / "scripts" / "pin_label.py"
    )
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _RefusedError(Exception):
    pass


def _pinned(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, served: dict[str, bytes]) -> Any:
    """The script, pointed at a copy of labels/ema-epi, fetching ``served`` by URL."""
    labels = tmp_path / "ema-epi"
    shutil.copytree(LABELS, labels)
    script = _script()
    monkeypatch.setattr(script, "LABELS", labels)
    monkeypatch.setattr(script, "LOCK", labels / "sources.lock.json")

    def fetch(url: str) -> bytes:
        if url not in served:
            raise _RefusedError("not served")
        return served[url]

    monkeypatch.setattr(
        script, "_fetcher", lambda: SimpleNamespace(fetch=fetch, RefusedError=_RefusedError)
    )
    return script


def _served() -> dict[str, bytes]:
    lock = json.loads((LABELS / "sources.lock.json").read_text(encoding="utf-8"))
    served: dict[str, bytes] = {}
    for entry in lock["sources"]:
        served[entry["url"]] = (LABELS / "sources" / entry["file"]).read_bytes()
        served[entry["list"]] = (LABELS / "lists" / entry["listFile"]).read_bytes()
    return served


def _run(script: Any, monkeypatch: pytest.MonkeyPatch, *argv: str) -> int:
    monkeypatch.setattr(sys, "argv", ["pin_label.py", *argv])
    status: int = script.main()
    return status


def test_re_pinning_the_same_bytes_changes_only_the_retrieval_dates(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    script = _pinned(tmp_path, monkeypatch, _served())
    before = (LABELS / "sources.lock.json").read_text(encoding="utf-8")
    assert _run(script, monkeypatch, "imatinib-teva-tablets-smpc-en.json") == 0

    today = datetime.datetime.now(datetime.UTC).date().isoformat()
    expected = json.loads(before)
    for entry in expected["sources"]:
        if entry["file"] == "imatinib-teva-tablets-smpc-en.json":
            entry["retrieved"] = today
        # The List the capsules share: one file, so both entries take its date.
        if entry["listFile"] == "imatinib-teva-smpc-en.list.json":
            entry["listRetrieved"] = today
    written = script.LOCK.read_text(encoding="utf-8")
    assert json.loads(written) == expected
    # Written as the committed lock is formatted, key order included.
    assert written == json.dumps(expected, indent=2, ensure_ascii=False) + "\n"
    assert (script.LABELS / "sources" / "imatinib-teva-tablets-smpc-en.json").read_bytes() == (
        LABELS / "sources" / "imatinib-teva-tablets-smpc-en.json"
    ).read_bytes()


def test_a_new_label_needs_every_value_the_bytes_do_not_carry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    script = _pinned(tmp_path, monkeypatch, _served())
    before = script.LOCK.read_bytes()
    assert _run(script, monkeypatch, "new-smpc-en.json", "--url", "https://x") == 1
    assert script.LOCK.read_bytes() == before


def test_a_document_that_is_not_its_urls_is_refused_and_nothing_written(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    served = _served()
    lock = json.loads((LABELS / "sources.lock.json").read_text(encoding="utf-8"))
    jentadueto, nuvaxovid = lock["sources"][0], lock["sources"][1]
    # Nuvaxovid's document served at Jentadueto's URL, with Nuvaxovid's List so the pair agrees.
    served[jentadueto["url"]] = served[nuvaxovid["url"]]
    served[jentadueto["list"]] = served[nuvaxovid["list"]]
    script = _pinned(tmp_path, monkeypatch, served)
    before = script.LOCK.read_bytes()
    assert _run(script, monkeypatch, jentadueto["file"]) == 1
    assert script.LOCK.read_bytes() == before


def test_metadata_refuses_a_list_that_does_not_list_the_document() -> None:
    script = _script()
    lock = json.loads((LABELS / "sources.lock.json").read_text(encoding="utf-8"))
    jentadueto, nuvaxovid = lock["sources"][0], lock["sources"][1]
    with pytest.raises(script.MetadataError, match="does not list the document"):
        script.metadata(
            (LABELS / "sources" / jentadueto["file"]).read_bytes(),
            (LABELS / "lists" / nuvaxovid["listFile"]).read_bytes(),
        )
