#!/usr/bin/env bash
# Names the commit whose history test/authority/lock.test.ts reads as released, and exports it as
# LOCK_BASE (docs/design/authority-import-contract.md, D10): for a push, the commit before the
# push; for anything else (a pull request, a manual run), main. The test then requires every
# importer version entry that ever appeared in that commit's first-parent history to be unchanged,
# so neither a second push nor a manual run can make a changed released entry pass.
#
# It fetches nothing: the job checks out full history (fetch-depth: 0) with no credentials kept,
# and a base missing from that history (a rewritten main) fails the step, a false failure.
set -euo pipefail

zero="0000000000000000000000000000000000000000"
if [[ "${GITHUB_EVENT_NAME:-}" == "push" && -n "${BEFORE_SHA:-}" && "${BEFORE_SHA}" != "$zero" ]]; then
  base="$BEFORE_SHA"
else
  base="refs/remotes/origin/main"
fi
commit="$(git rev-parse --verify "${base}^{commit}")"
echo "LOCK_BASE=$commit" >> "$GITHUB_ENV"
echo "importer lock base: $commit"
