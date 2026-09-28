#!/usr/bin/env python
"""Generate the pydantic models from ``contracts/generated/*.schema.json``.

Mirrors ``scripts/ci/check-generated.mjs`` in spirit: ``--check`` regenerates into a temporary
directory and fails if any committed file would change, so a contract change that was not
regenerated and committed cannot pass the gate. Unlike the Node script this compares content
rather than modification times, because the generator writes every module unconditionally.

Determinism: ``--disable-timestamp`` removes the only clock-dependent line datamodel-code-generator
emits; everything else is a pure function of the input schema, the generator version, and the
flags below, all of which are exact-pinned in ``pyproject.toml`` / ``uv.lock``.

Strictness. datamodel-code-generator maps ``additionalProperties: false`` to
``ConfigDict(extra="forbid")`` and an open object to ``ConfigDict(extra="allow")`` by itself;
``tests/test_contracts_strictness.py`` proves that for every object in every schema rather than
trusting it. Two things it does not do by itself are configured below (audit C-7), because
pydantic's lax mode accepted what Zod refuses and dumped it back as another document:
``--strict-types`` (a string ``"1"`` or ``true`` is not an integer, ``1`` is not a string or a
boolean) and ``--base-class zone_a.contract_model.ContractModel``, which refuses ``null`` for an
optional field. ``tests/test_contract_verdicts.py`` holds the models to Zod's verdicts.

Which contracts: every one ``contracts/generated/index.json`` lists except those Zone A does not
read (``NOT_ZONE_A``), so a new contract is generated unless it is named there.
"""

from __future__ import annotations

import argparse
import filecmp
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCHEMAS = ROOT.parent / "contracts" / "generated"
OUTPUT = ROOT / "src" / "zone_a" / "contracts"

# The contracts Zone A neither writes nor reads: the query service's tool surface and the agent's
# turn record, whose readers are the agent (its vendored copies) and people.
NOT_ZONE_A = frozenset({"agent-turn", "query-tools"})


def zone_a_contracts() -> list[str]:
    """The contracts to generate, one module each: every one the index lists but ``NOT_ZONE_A``.

    Returns:
        The contract names, sorted.
    """
    index = json.loads((SCHEMAS / "index.json").read_text(encoding="utf-8"))
    return sorted(entry["name"] for entry in index["contracts"] if entry["name"] not in NOT_ZONE_A)


# One module per contract root, named after the schema file.
CONTRACTS = zone_a_contracts()

HEADER = '''"""Generated pydantic models. Do not edit.

Regenerate with ``uv run python scripts/generate_models.py``; ``--check`` fails the build when a
committed module is out of date with ``contracts/generated/``.
"""

'''


def module_name(contract: str) -> str:
    """The Python module name for a contract: its schema name with ``_`` for ``-``."""
    return contract.replace("-", "_")


def generate_into(destination: Path) -> None:
    """Generates one formatted module per contract, and the package's ``__init__.py``.

    Raises:
        SystemExit: A contract schema is missing.
        subprocess.CalledProcessError: The generator or the formatter failed.
    """
    destination.mkdir(parents=True, exist_ok=True)
    for contract in CONTRACTS:
        schema = SCHEMAS / f"{contract}.schema.json"
        if not schema.is_file():
            raise SystemExit(f"Contract schema is missing: {schema}")
        target = destination / f"{module_name(contract)}.py"
        subprocess.run(  # a fixed argument vector, never a shell string
            [
                sys.executable,
                "-m",
                "datamodel_code_generator",
                "--input",
                str(schema),
                "--input-file-type",
                "jsonschema",
                "--output",
                str(target),
                "--output-model-type",
                "pydantic_v2.BaseModel",
                "--target-python-version",
                "3.14",
                # The contracts pin `uuid` and `date-time` with a regex *and* a JSON Schema
                # `format`. Left alone, datamodel-code-generator emits `Annotated[UUID,
                # Field(pattern=...)]` and `Annotated[AwareDatetime, Field(pattern=...)]`, and
                # pydantic cannot apply a string pattern to a uuid or datetime schema: every
                # such field raises TypeError on the first validation. Mapping the two formats
                # back to `str` keeps the regex — which is the stricter of the two constraints
                # anyway — and keeps the value byte-identical through a round trip.
                "--type-mappings",
                "string+uuid=string",
                "string+date-time=string",
                "--disable-timestamp",
                # Zod's types, never pydantic's lax coercions (audit C-7).
                "--strict-types",
                "str",
                "int",
                "float",
                "bool",
                "--base-class",
                "zone_a.contract_model.ContractModel",
                "--use-annotated",
                "--use-standard-collections",
                "--use-union-operator",
                "--formatters",
                "builtin",
            ],
            check=True,
            cwd=ROOT,
        )
        target.write_text(HEADER + target.read_text(encoding="utf-8"), encoding="utf-8")
        # Formatted with an explicit config path, never a discovered one: ruff would otherwise
        # fall back to its 88-column default when the destination lies outside the project (as
        # it does in --check mode), and the two runs would disagree for that reason alone.
        subprocess.run(  # a fixed argument vector, never a shell string
            [
                sys.executable,
                "-m",
                "ruff",
                "format",
                "--quiet",
                "--config",
                str(ROOT / "pyproject.toml"),
                str(target),
            ],
            check=True,
            cwd=ROOT,
        )

    (destination / "__init__.py").write_text(
        '"""Pydantic models generated from the published Zone B contracts."""\n', encoding="utf-8"
    )


def check() -> int:
    """Regenerates into a temporary directory and compares it with the committed modules.

    Returns:
        The exit status: 0 when every committed module is up to date, 1 when a module is
        missing, extra or different.
    """
    with tempfile.TemporaryDirectory() as temporary:
        candidate = Path(temporary) / "contracts"
        generate_into(candidate)
        expected = sorted(path.name for path in candidate.glob("*.py"))
        actual = sorted(path.name for path in OUTPUT.glob("*.py"))
        if expected != actual:
            print(f"Generated modules differ: expected {expected}, found {actual}")
            return 1
        match, mismatch, errors = filecmp.cmpfiles(candidate, OUTPUT, expected, shallow=False)
        stale = sorted(mismatch + errors)
        if stale:
            print(
                "Generated models are out of date; run scripts/generate_models.py and commit "
                "the result:\n" + "\n".join(f"  {name}" for name in stale)
            )
            return 1
        print(f"Generated models are up to date ({len(match)} modules checked)")
        return 0


def main() -> int:
    """Regenerates the models in place, or with ``--check`` fails if they are out of date.

    Returns:
        The exit status: 0 when the models were generated or are up to date, 1 otherwise.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check",
        action="store_true",
        help="fail if regenerating would change any committed module",
    )
    arguments = parser.parse_args()
    if arguments.check:
        return check()
    shutil.rmtree(OUTPUT, ignore_errors=True)
    generate_into(OUTPUT)
    print(f"Generated {len(CONTRACTS)} contract modules in {OUTPUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
