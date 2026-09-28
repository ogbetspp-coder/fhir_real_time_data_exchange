"""The weekly EMA drift check fetches as the gate does (scripts/check_label_sources.py)."""

from __future__ import annotations

import importlib.util
import json
import re
from pathlib import Path
from types import ModuleType

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


def test_a_list_two_labels_share_is_fetched_once() -> None:
    script = _script()
    lock = json.loads((ROOT / "labels" / "ema-epi" / "sources.lock.json").read_text("utf-8"))
    urls = [url for url, _, _ in script.files(lock)]
    assert len(urls) == len(set(urls))
    every = {entry[key] for entry in lock["sources"] for key in ("url", "list")}
    assert set(urls) == every
