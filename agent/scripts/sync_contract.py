"""Vendor the published contracts into the package, or fail if a copy has drifted.

The agent must be self-contained on Agent Engine — the repository root is not there — but the
contracts are published once, in ``contracts/generated/``, and a second hand-maintained copy
would be a second source of truth. So each copy is generated and gate-checked, exactly as
``zone-a/scripts/generate_models.py`` does for the pydantic models.

Two schemas are vendored: ``query-tools`` (what the agent validates every tool result against)
and ``agent-turn`` (what the agent's own turn record is validated against in tests).

    uv run --frozen python scripts/sync_contract.py           # copy both
    uv run --frozen python scripts/sync_contract.py --check   # fail on drift in either
"""

from __future__ import annotations

import sys
from pathlib import Path

AGENT_ROOT = Path(__file__).resolve().parents[1]
REPOSITORY_ROOT = AGENT_ROOT.parent
PUBLISHED = REPOSITORY_ROOT / "contracts" / "generated"
PACKAGE = AGENT_ROOT / "src" / "verifiable_answer_agent"
VENDORED = PACKAGE / "contracts"

CONTRACTS: tuple[str, ...] = ("query-tools.schema.json", "agent-turn.schema.json")


def main(argv: list[str]) -> int:
    """Copies the published contracts into the package, or with ``--check`` compares them.

    Args:
        argv: The command line, program name first.

    Returns:
        The exit status: 0 when every copy was written or is current, 1 when a vendored copy
        differs from the published one, 2 when a published contract is missing.
    """
    check = "--check" in argv[1:]
    status = 0
    for name in CONTRACTS:
        source = PUBLISHED / name
        destination = VENDORED / name
        if not source.is_file():
            print(f"contract not found: {source}", file=sys.stderr)
            return 2
        published = source.read_bytes()
        if check:
            current = destination.read_bytes() if destination.is_file() else b""
            if current != published:
                print(
                    f"the vendored contract differs from contracts/generated/{name};"
                    " run scripts/sync_contract.py and commit the result",
                    file=sys.stderr,
                )
                status = 1
            continue
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(published)
        print(f"wrote {destination.relative_to(AGENT_ROOT)}")
    if check and status == 0:
        print("vendored contracts are current")
    return status


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
