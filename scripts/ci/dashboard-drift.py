#!/usr/bin/env python3
"""Compares the operations dashboard as configured with the live one (audit I-6).

  dashboard-drift.py <configured json> <live json>

The Monitoring API rewrites a dashboard's JSON into its own form: it fills in defaults (a chart's
time shift, a text widget's style), drops zero values (a tile at xPos 0 has no xPos), and adds
its own name and etag. Comparing the two texts therefore showed a change on every Terraform plan,
so infra/observability.tf ignores the text and this compares meaning instead: every value the
configuration sets must be present, and equal, in the live dashboard. What the API adds is
ignored; a configured zero value may be absent. A list must have the same length and match item
by item, because the order of tiles and data sets is meaningful.

Prints each differing path, never a value. Exit codes: 0 the live dashboard carries the
configuration; 1 it does not (the deploy replaces it); 2 anything else — an input that could not
be read, or any failure of this script — which the deploy treats as an error, never as drift.
"""
import json
import sys

ZERO = (0, 0.0, False, "", [], {})


def number(value):
    """A JSON number, or a string the API used for one (proto3 JSON writes int64 as a string)."""
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return value
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return None
    return None


def differences(configured, live, path="$"):
    if isinstance(configured, dict):
        if not isinstance(live, dict):
            return [path]
        found = []
        for key, value in configured.items():
            where = f"{path}.{key}"
            if key not in live:
                if not any(value == zero and type(value) is type(zero) for zero in ZERO):
                    found.append(where)
                continue
            found.extend(differences(value, live[key], where))
        return found
    if isinstance(configured, list):
        if not isinstance(live, list) or len(live) != len(configured):
            return [path]
        found = []
        for index, (want, have) in enumerate(zip(configured, live)):
            found.extend(differences(want, have, f"{path}[{index}]"))
        return found
    if isinstance(configured, bool) or isinstance(live, bool):
        return [] if configured is live else [path]
    want, have = number(configured), number(live)
    if isinstance(configured, (int, float)) and want is not None and have is not None:
        return [] if want == have else [path]
    return [] if configured == live else [path]


def main():
    try:
        with open(sys.argv[1], encoding="utf-8") as handle:
            configured = json.load(handle)
        with open(sys.argv[2], encoding="utf-8") as handle:
            live = json.load(handle)
    except (IndexError, OSError, ValueError) as error:
        print(f"dashboard drift: could not read the inputs ({type(error).__name__})")
        return 2
    if not isinstance(configured, dict) or not isinstance(live, dict):
        print("dashboard drift: an input is not a JSON object")
        return 2
    try:
        found = differences(configured, live)
    except Exception as error:  # noqa: BLE001 -- any failure to compare is an error, never drift
        print(f"dashboard drift: could not compare ({type(error).__name__})")
        return 2
    if not found:
        print("dashboard drift: none; the live dashboard carries every configured value")
        return 0
    print(f"dashboard drift: {len(found)} configured value(s) missing or different in the live dashboard")
    for where in found[:20]:
        print(f"  {where}")
    return 1


if __name__ == "__main__":
    # An uncaught error would exit 1, which reads as "drift" and replaces the dashboard; it is an
    # error, 2, whatever it is.
    try:
        sys.exit(main())
    except SystemExit:
        raise
    except BaseException as error:  # noqa: BLE001
        print(f"dashboard drift: failed ({type(error).__name__})")
        sys.exit(2)
