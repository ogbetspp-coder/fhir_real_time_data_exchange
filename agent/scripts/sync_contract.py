"""Vendor the published contracts into the package, or fail if a copy has drifted.

The agent must be self-contained on Agent Engine — the repository root is not there — but the
contracts are published once, in ``contracts/generated/``, and a second hand-maintained copy
would be a second source of truth. So each copy is generated and gate-checked, exactly as
``zone-a/scripts/generate_models.py`` does for the pydantic models.

Two schemas are vendored: ``query-tools`` (what the agent validates every tool result against)
and ``agent-turn`` (what the agent's own turn record is validated against in tests). So is one
table, ``code-points.json``: the gap, Default_Ignorable and word-character classes the fidelity
vectors record for every code point (``test/fixtures/fidelity/vectors.json`` ``codePoints``),
which ``quote_edge`` reads instead of a hand copy of the lists.

    uv run --frozen python scripts/sync_contract.py           # write every copy
    uv run --frozen python scripts/sync_contract.py --check   # fail on drift in any
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

AGENT_ROOT = Path(__file__).resolve().parents[1]
REPOSITORY_ROOT = AGENT_ROOT.parent
PUBLISHED = REPOSITORY_ROOT / "contracts" / "generated"
VECTORS = REPOSITORY_ROOT / "test" / "fixtures" / "fidelity" / "vectors.json"
PACKAGE = AGENT_ROOT / "src" / "verifiable_answer_agent"
VENDORED = PACKAGE / "contracts"

CONTRACTS: tuple[str, ...] = ("query-tools.schema.json", "agent-turn.schema.json")
CODE_POINTS = "code-points.json"
CODE_POINT_CLASSES: tuple[str, ...] = ("gap", "defaultIgnorable", "wordCharacter")


def code_points(vectors: bytes) -> bytes:
    """The vendored ``code-points.json``, from the fidelity vectors.

    Each class is the list of code points where membership flips, starting outside at U+0000:
    a code point is in the class when an odd number of the list's entries are at or below it.

    Args:
        vectors: ``test/fixtures/fidelity/vectors.json``, as bytes.

    Returns:
        The file's bytes, one class per line.
    """
    table = json.loads(vectors)["codePoints"]
    lines = []
    for name in CODE_POINT_CLASSES:
        bit = 1 << table["classes"].index(name)
        flips: list[int] = []
        for start, bits in table["runs"]:
            if bool(bits & bit) != (len(flips) % 2 == 1):
                flips.append(start)
        lines.append(f"  {json.dumps(name)}: {json.dumps(flips, separators=(',', ':'))}")
    return ("{\n" + ",\n".join(lines) + "\n}\n").encode()


def main(argv: list[str]) -> int:
    """Writes every vendored copy into the package, or with ``--check`` compares them.

    Args:
        argv: The command line, program name first.

    Returns:
        The exit status: 0 when every copy was written or is current, 1 when a vendored copy
        differs from its source, 2 when a source is missing.
    """
    check = "--check" in argv[1:]
    sources = {name: PUBLISHED / name for name in CONTRACTS} | {CODE_POINTS: VECTORS}
    for source in sources.values():
        if not source.is_file():
            print(f"source not found: {source}", file=sys.stderr)
            return 2
    status = 0
    for name, source in sources.items():
        expected = source.read_bytes()
        if name == CODE_POINTS:
            expected = code_points(expected)
        destination = VENDORED / name
        if check:
            current = destination.read_bytes() if destination.is_file() else b""
            if current != expected:
                print(
                    f"the vendored {name} differs from {source.relative_to(REPOSITORY_ROOT)};"
                    " run scripts/sync_contract.py and commit the result",
                    file=sys.stderr,
                )
                status = 1
            continue
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(expected)
        print(f"wrote {destination.relative_to(AGENT_ROOT)}")
    if check and status == 0:
        print("vendored contracts are current")
    return status


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
