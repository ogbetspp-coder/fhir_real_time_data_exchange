"""The conservation check's tests hold it: every fault made in it is caught, or cannot matter.

``scripts/mutate_checker.py`` makes each small fault in ``certify.py`` in turn and runs the
check's tests against it; ``docs/checker-mutants.json`` records the run. These tests hold the
record to the code: it is the run of this ``certify.py`` (its SHA-256 and its count of faults)
with these tests and this corpus (their SHA-256), and every fault the tests did not catch is one
recorded as unable to change what the check does, with the reason, which names that one fault.
A change to the check, its tests or the corpus needs the script run again.
"""

from __future__ import annotations

import hashlib
import json

from mutate_checker import EQUIVALENT, RECORD, TARGET, count, held_sha256, keys


def test_the_record_is_the_run_of_the_check_as_it_is() -> None:
    record = json.loads(RECORD.read_text("utf-8"))
    assert record["targetSha256"] == hashlib.sha256(TARGET.read_bytes()).hexdigest(), (
        "certify.py changed: run scripts/mutate_checker.py --write"
    )
    assert record["mutants"] == count()
    assert record["testsSha256"] == held_sha256(), (
        "the check's tests or the corpus changed: run scripts/mutate_checker.py --write"
    )


def test_every_fault_is_caught_or_recorded_as_unable_to_matter() -> None:
    record = json.loads(RECORD.read_text("utf-8"))
    unexplained = [s for s in record["survivors"] if not s["equivalent"]]
    assert not unexplained, unexplained
    assert record["killed"] + len(record["survivors"]) == record["mutants"]
    # Each reason recorded is one a survivor needed: no stale excuses.
    used = {(s["function"], s["code"], s["kind"], s["occurrence"]) for s in record["survivors"]}
    assert set(EQUIVALENT) <= used, set(EQUIVALENT) - used


def test_each_reason_names_one_fault_of_the_check_as_it_is() -> None:
    # A reason written for one fault never excuses another: every name is one fault's.
    names = keys()
    assert len(set(names)) == len(names)
    assert set(EQUIVALENT) <= set(names), set(EQUIVALENT) - set(names)


def test_most_faults_are_caught_outright() -> None:
    record = json.loads(RECORD.read_text("utf-8"))
    assert record["killed"] / record["mutants"] > 0.9
