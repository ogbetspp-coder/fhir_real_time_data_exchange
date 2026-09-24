#!/usr/bin/env bash
# Fetches the commit test/authority/lock.test.ts compares the importer lock with, and exports it
# as LOCK_BASE (docs/design/authority-import-contract.md, D10): for a push, the commit before the
# push, so a push to main cannot compare main with itself; for anything else (a pull request, a
# manual run), main. Run in CI only; a local checkout compares with its own origin/main.
set -euo pipefail

zero="0000000000000000000000000000000000000000"
if [[ "${GITHUB_EVENT_NAME:-}" == "push" && -n "${BEFORE_SHA:-}" && "${BEFORE_SHA}" != "$zero" ]]; then
  git fetch --no-tags --depth=1 origin "$BEFORE_SHA"
  base="$BEFORE_SHA"
else
  git fetch --no-tags --depth=1 origin main:refs/remotes/origin/main
  base="$(git rev-parse refs/remotes/origin/main)"
fi
echo "LOCK_BASE=$base" >> "$GITHUB_ENV"
echo "importer lock base: $base"
