"""scripts/pin_label.py writes the label lock; here against a copy, with the network stubbed."""

from __future__ import annotations

import datetime
import importlib.util
import json
import shutil
import sys
from collections.abc import Callable
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
        # The date the script records is the fetch's own, so it is no longer reconstructed.
        if entry["file"] == "imatinib-teva-tablets-smpc-en.json":
            entry["retrieved"] = today
            del entry["retrievedReconstructed"]
        # The List the capsules share: one file, so both entries take its date. The capsules'
        # own document date stays as it was, reconstructed.
        if entry["listFile"] == "imatinib-teva-smpc-en.list.json":
            entry["listRetrieved"] = today
            del entry["listRetrievedReconstructed"]
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


TABLETS = "imatinib-teva-tablets-smpc-en.json"
CAPSULES = "imatinib-teva-smpc-en.json"
CAPSULES_LIST = "imatinib-teva-smpc-en.list.json"


def _entry(file: str) -> dict[str, Any]:
    lock = json.loads((LABELS / "sources.lock.json").read_text(encoding="utf-8"))
    entry: dict[str, Any] = next(each for each in lock["sources"] if each["file"] == file)
    return entry


def _written(script: Any) -> dict[str, bytes]:
    """Every file in the copy of labels/ema-epi the script may write, by relative path."""
    return {
        str(path.relative_to(script.LABELS)): path.read_bytes()
        for path in sorted(script.LABELS.rglob("*"))
        if path.is_file()
    }


def _shared_list(change: Callable[[dict[str, Any]], None]) -> bytes:
    listing: dict[str, Any] = json.loads((LABELS / "lists" / CAPSULES_LIST).read_bytes())
    change(listing)
    return json.dumps(listing).encode()


def _set_procedure(listing: dict[str, Any]) -> None:
    for each in listing["subject"]["extension"]:
        if each["url"].endswith("/procedureNumber"):
            each["valueIdentifier"]["value"] = "EMEA/H/C/002585/IB/0099"


def _unlist_capsules(listing: dict[str, Any]) -> None:
    capsules = "Bundle/" + _entry(CAPSULES)["url"].rsplit("/", 1)[-1]
    listing["entry"] = [each for each in listing["entry"] if each["item"]["reference"] != capsules]


@pytest.mark.parametrize(
    ("change", "reason"),
    [
        # The tablets' entry would take the new procedure number; the capsules' records the old.
        (_set_procedure, "procedureNumber is not the one its entry records"),
        (_unlist_capsules, "does not list the document"),
    ],
)
def test_a_shared_list_is_re_pinned_only_if_it_still_holds_every_sharing_label(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    change: Callable[[dict[str, Any]], None],
    reason: str,
) -> None:
    served = _served()
    served[_entry(TABLETS)["list"]] = _shared_list(change)
    script = _pinned(tmp_path, monkeypatch, served)
    before = _written(script)
    assert _run(script, monkeypatch, TABLETS) == 1
    out = capsys.readouterr().out
    assert CAPSULES in out
    assert reason in out
    assert _written(script) == before


def test_a_new_list_url_for_a_shared_list_file_needs_its_own_list_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    guid = "00000000-0000-4000-8000-0000000000aa"
    url = f"https://epi.ema.europa.eu/consuming/api/fhir/List/{guid}"

    def moved(listing: dict[str, Any]) -> None:
        listing["id"] = guid

    served = _served()
    served[url] = _shared_list(moved)
    script = _pinned(tmp_path, monkeypatch, served)
    before = _written(script)
    assert _run(script, monkeypatch, TABLETS, "--list", url) == 1
    assert "needs --list-file" in capsys.readouterr().out
    assert _written(script) == before

    # With a List file of its own the label moves, and the capsules keep theirs untouched.
    own = "imatinib-teva-tablets-smpc-en.list.json"
    assert _run(script, monkeypatch, TABLETS, "--list", url, "--list-file", own) == 0
    lock = json.loads(script.LOCK.read_text(encoding="utf-8"))
    by_file = {each["file"]: each for each in lock["sources"]}
    assert (by_file[TABLETS]["list"], by_file[TABLETS]["listFile"]) == (url, own)
    assert "listRetrievedReconstructed" not in by_file[TABLETS]
    assert by_file[CAPSULES] == _entry(CAPSULES)
    assert (script.LABELS / "lists" / CAPSULES_LIST).read_bytes() == (
        LABELS / "lists" / CAPSULES_LIST
    ).read_bytes()


@pytest.mark.parametrize(
    "argv",
    [
        ["../sources.lock.json"],
        ["sources/brukinsa-smpc-en.json"],
        ["Brukinsa.json"],
        ["brukinsa-smpc-en.json", "--list-file", "../sources/brukinsa-smpc-en.json"],
        ["brukinsa-smpc-en.json", "--list-file", "brukinsa.json"],
    ],
)
def test_a_file_name_that_is_not_a_plain_name_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, argv: list[str]
) -> None:
    script = _pinned(tmp_path, monkeypatch, _served())
    before = _written(script)
    assert _run(script, monkeypatch, *argv) == 1
    assert _written(script) == before
