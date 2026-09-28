"""The weekly EMA drift check fetches as the gate does (scripts/check_label_sources.py)."""

from __future__ import annotations

import importlib.util
import json
import re
from pathlib import Path
from types import ModuleType

import pytest

ROOT = Path(__file__).resolve().parents[2]


def _script() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "check_label_sources", ROOT / "zone-a" / "scripts" / "check_label_sources.py"
    )
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_drift_check_fetches_with_the_gates_header_limits_and_no_redirect() -> None:
    script = _script()
    fetcher = (ROOT / "src" / "authority" / "fetch.ts").read_text(encoding="utf-8")
    assert f'headers: {{ Accept: "{script.ACCEPT}" }}' in fetcher
    assert 'redirect: "error"' in fetcher
    assert "response.status !== 200" in fetcher
    limit = re.search(r"MAX_AUTHORITY_BYTES = (\d+) \* 1024 \* 1024;", fetcher)
    assert limit is not None
    assert int(limit.group(1)) * 1024 * 1024 == script.MAX_BYTES
    timeout = re.search(r"TIMEOUT_MS = ([\d_]+);", fetcher)
    assert timeout is not None
    assert int(timeout.group(1).replace("_", "")) == script.TIMEOUT_SECONDS * 1000


def test_only_a_url_the_gate_fetches_is_fetched() -> None:
    script = _script()
    fetcher = (ROOT / "src" / "authority" / "fetch.ts").read_text(encoding="utf-8")
    assert 'const EMA_HOST = "epi.ema.europa.eu";' in fetcher
    assert "const EMA_BASE = `https://${EMA_HOST}/consuming/api/fhir`;" in fetcher
    assert "const GUID = /^[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}$/;" in fetcher
    lock = json.loads((ROOT / "labels" / "ema-epi" / "sources.lock.json").read_text("utf-8"))
    for url, _, _ in script.files(lock):
        assert script.URL.fullmatch(url), url
    guid = "6301d093-9501-4e2e-a500-794dd263f9a2"
    for url in (
        f"http://epi.ema.europa.eu/consuming/api/fhir/Bundle/{guid}",
        f"https://epi.ema.europa.eu.example/consuming/api/fhir/Bundle/{guid}",
        f"https://epi.ema.europa.eu/consuming/api/fhir/Binary/{guid}",
        f"https://epi.ema.europa.eu/consuming/api/fhir/Bundle/{guid}?x=1",
        "https://epi.ema.europa.eu/consuming/api/fhir/Bundle/not-a-guid",
        "file:///etc/passwd",
    ):
        # Refused before any network access.
        with pytest.raises(script.RefusedError, match="not a URL the gate fetches"):
            script.fetch(url)


def test_a_list_two_labels_share_is_fetched_once() -> None:
    script = _script()
    lock = json.loads((ROOT / "labels" / "ema-epi" / "sources.lock.json").read_text("utf-8"))
    urls = [url for url, _, _ in script.files(lock)]
    assert len(urls) == len(set(urls))
    every = {entry[key] for entry in lock["sources"] for key in ("url", "list")}
    assert set(urls) == every
