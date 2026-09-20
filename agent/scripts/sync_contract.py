"""Vendor the published tool contract into the package, or fail if the copy has drifted.

The agent must be self-contained on Agent Engine — the repository root is not there — but the
contract is published once, in ``contracts/generated/``, and a second hand-maintained copy
would be a second source of truth. So the copy is generated and gate-checked, exactly as
``zone-a/scripts/generate_models.py`` does for the pydantic models.

    uv run --frozen python scripts/sync_contract.py           # copy
    uv run --frozen python scripts/sync_contract.py --check   # fail on drift
"""

from __future__ import annotations

import sys
from pathlib import Path

AGENT_ROOT = Path(__file__).resolve().parents[1]
REPOSITORY_ROOT = AGENT_ROOT.parent
SOURCE = REPOSITORY_ROOT / "contracts" / "generated" / "query-tools.schema.json"
PACKAGE = AGENT_ROOT / "src" / "verifiable_answer_agent"
DESTINATION = PACKAGE / "contracts" / "query-tools.schema.json"


def main(argv: list[str]) -> int:
    check = "--check" in argv[1:]
    if not SOURCE.is_file():
        print(f"contract not found: {SOURCE}", file=sys.stderr)
        return 2
    published = SOURCE.read_bytes()
    if check:
        current = DESTINATION.read_bytes() if DESTINATION.is_file() else b""
        if current != published:
            print(
                "the vendored contract differs from contracts/generated/query-tools.schema.json;"
                " run scripts/sync_contract.py and commit the result",
                file=sys.stderr,
            )
            return 1
        print("vendored contract is current")
        return 0
    DESTINATION.parent.mkdir(parents=True, exist_ok=True)
    DESTINATION.write_bytes(published)
    print(f"wrote {DESTINATION.relative_to(AGENT_ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
