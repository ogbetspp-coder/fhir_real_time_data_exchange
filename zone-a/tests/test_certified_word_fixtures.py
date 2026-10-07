"""The recompute's results the TypeScript importer is held to are this build's.

``scripts/certified_word_fixtures.py`` writes them; ``test/certified-word/`` reads them.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path
from types import ModuleType

import pytest

from zone_a import recompute

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"


def _script() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "certified_word_fixtures", SCRIPTS / "certified_word_fixtures.py"
    )
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_committed_results_are_what_this_build_writes() -> None:
    script = _script()
    committed = {path.name: path.read_bytes() for path in script.TARGET.glob("*")}
    assert committed == script.render()


def test_each_result_is_the_commands_and_the_labels_are_synthetic() -> None:
    script = _script()
    files = script.render()
    cases = json.loads(files["cases.json"])
    assert [case["name"] for case in cases] == [name for name, *_ in script._cases()]
    for case in cases:
        written = files[f"{case['name']}.json"].decode("utf-8")
        result = json.loads(written)
        if case["name"] == "smpc-refused":
            assert result == {"refusal": {"code": "section", "detail": "refused: smpc.4.2"}}
            continue
        assert written.endswith("\n")
        # Each result is of its committed label, which the gate's tests read as the upload (D4).
        label = files[f"{case['name']}.docx"]
        assert result["source"] == {
            "sha256": hashlib.sha256(label).hexdigest(),
            "bytes": len(label),
        }
        assert result["versions"] == recompute.versions(case["request"]["document"])
        assert all(s["refusal"] is None for s in result["sections"])
        # Every narrative says it is synthetic (test/synthetic-only.test.ts).
        assert all(
            "not for clinical use" in s["narrative"]
            for s in result["sections"]
            if s["narrative"] is not None
        )
        assert case["documentId"].startswith(script.BLOCK)


def test_a_drifted_file_fails_the_check(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    script = _script()
    monkeypatch.setattr(script, "TARGET", tmp_path)
    monkeypatch.setattr("sys.argv", ["certified_word_fixtures.py"])
    assert script.main() == 0
    (tmp_path / "stale.json").write_text("{}\n", encoding="utf-8")
    monkeypatch.setattr("sys.argv", ["certified_word_fixtures.py", "--check"])
    assert script.main() == 1
    (tmp_path / "stale.json").unlink()
    assert script.main() == 0
    (tmp_path / "smpc.json").write_text("{}\n", encoding="utf-8")
    assert script.main() == 1
    # A carriage return a text read would fold away is drift too (review of #193).
    monkeypatch.setattr("sys.argv", ["certified_word_fixtures.py"])
    assert script.main() == 0
    written = (tmp_path / "smpc.json").read_bytes()
    (tmp_path / "smpc.json").write_bytes(written.replace(b"\n", b"\r\n"))
    monkeypatch.setattr("sys.argv", ["certified_word_fixtures.py", "--check"])
    assert script.main() == 1
    monkeypatch.setattr("sys.argv", ["certified_word_fixtures.py"])
    (tmp_path / "stale.json").write_text("{}\n", encoding="utf-8")
    assert script.main() == 0
    assert not (tmp_path / "stale.json").exists()
    assert (tmp_path / "smpc.json").read_text(encoding="utf-8") != "{}\n"
