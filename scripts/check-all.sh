#!/usr/bin/env bash
# Every gate .github/workflows/ci.yml runs on a pull request, in one local command: the Node gate
# (the `check` job), then the Zone A and Agent jobs, step for step. The official HL7 validator
# (`npm run validate:official`) is left out: it needs Java 21 and ~200 MB of downloads, and it is
# its own CI job for the same reason. The renderer image (`npm run renderer:image`,
# `renderer:smoke`, `renderer:check`, `renderer:fonts` and `renderer:drawings`) is left out too:
# it needs Docker and ~200 MB of downloads, and it is its own CI job. test/ci/check-all.test.ts fails if a CI step is
# missing here, so the two cannot drift apart silently.
#
# uv is taken from $UV, then agent/.uv-bootstrap/bin/uv, then zone-a/.uv-bootstrap/bin/uv, then
# PATH (agent/README.md shows how to make a bootstrap one). Python 3.14 must be installed.
#
# usage: scripts/check-all.sh
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

fail() {
  printf 'check-all: %s\n' "$1" >&2
  exit 1
}

step() {
  printf '\n==> %s\n' "$1"
}

UV="${UV:-}"
if [ -z "$UV" ]; then
  for candidate in agent/.uv-bootstrap/bin/uv zone-a/.uv-bootstrap/bin/uv; do
    if [ -x "$candidate" ]; then
      UV="$ROOT/$candidate"
      break
    fi
  done
fi
if [ -z "$UV" ]; then
  UV="$(command -v uv || true)"
fi
[ -n "$UV" ] && [ -x "$UV" ] ||
  fail "uv not found. Install uv 0.12.17 (agent/README.md, \"Set-up\") or set UV=/path/to/uv."
# `uv python find` only looks; it never downloads an interpreter.
"$UV" python find 3.14 >/dev/null 2>&1 ||
  fail "Python 3.14 not found. CI pins 3.14 (.python-version; agent/README.md, \"Why 3.14\"); install it first."
command -v npm >/dev/null 2>&1 || fail "npm not found. Install Node $(cat .nvmrc) (.nvmrc)."

# --- Node (job: check) ---------------------------------------------------------------------
step "Node: npm run check"
npm run check
step "Node: npm run build"
npm run build

# --- Zone A (job: zone-a) ------------------------------------------------------------------
step "Zone A: uv sync --frozen"
(cd zone-a && "$UV" sync --frozen)
step "Zone A: uv run --frozen ruff check ."
(cd zone-a && "$UV" run --frozen ruff check .)
step "Zone A: uv run --frozen ruff format --check ."
(cd zone-a && "$UV" run --frozen ruff format --check .)
step "Zone A: uv run --frozen mypy --strict"
(cd zone-a && "$UV" run --frozen mypy --strict)
step "Zone A: uv run --frozen python scripts/generate_models.py --check"
(cd zone-a && "$UV" run --frozen python scripts/generate_models.py --check)
step "Zone A: differential corpus from the TypeScript implementation"
npx tsx scripts/fidelity/differential.ts --seed 20260920 --count 2000 > differential.jsonl
step "Zone A: uv run --frozen pytest"
(cd zone-a && DIFFERENTIAL_CORPUS="$ROOT/differential.jsonl" "$UV" run --frozen pytest)

# --- Agent (job: agent) --------------------------------------------------------------------
step "Agent: uv sync --frozen"
(cd agent && "$UV" sync --frozen)
step "Agent: uv run --frozen ruff check ."
(cd agent && "$UV" run --frozen ruff check .)
step "Agent: uv run --frozen ruff format --check ."
(cd agent && "$UV" run --frozen ruff format --check .)
step "Agent: uv run --frozen mypy --strict"
(cd agent && "$UV" run --frozen mypy --strict)
step "Agent: uv run --frozen python scripts/sync_contract.py --check"
(cd agent && "$UV" run --frozen python scripts/sync_contract.py --check)
step "Agent: uv run --frozen pytest --cov"
(cd agent && "$UV" run --frozen pytest --cov)

printf '\ncheck-all: every CI gate passed (official HL7 validation and the renderer image not run; see header).\n'
