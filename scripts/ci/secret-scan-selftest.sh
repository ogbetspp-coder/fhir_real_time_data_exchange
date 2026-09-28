#!/usr/bin/env bash
# Proves the secret scan's verdict (scripts/ci/secret-scan.sh; docs/foundations.md, D4) on planted
# secrets, with the real pinned scanner, before the Vulnerabilities workflow trusts a clean scan.
# Each case is a throwaway repository holding a fresh token of GitHub's shape, in a place the scan
# once missed; each must fail the scan, naming the rule or refusing as stated below. A clean
# repository with a merge in its history must pass, so a scan that fails on everything cannot
# pass this either.
#
#   - a secret a merge commit introduces and the next commit removes, in the range and in --all:
#     git log prints no patch for a merge unless asked (-m);
#   - a secret on a line marked `gitleaks:allow`: gitleaks honours the comment unless told not to;
#   - a secret whose fingerprint a committed .gitleaksignore lists: gitleaks reads the one at the
#     scanned root whatever it is told, so the scan refuses to run while one is there (this case
#     must be refused, not found).
#
# The token is generated at run time, so this file carries none for the scan of this repository
# to find, and the planted repositories live in a temporary directory that is deleted on exit.
#
#   bash scripts/ci/secret-scan-selftest.sh
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

# No user or system git configuration: the planted repositories are built the same everywhere.
export GIT_CONFIG_GLOBAL=/dev/null GIT_CONFIG_NOSYSTEM=1
export GIT_AUTHOR_NAME=selftest GIT_AUTHOR_EMAIL=selftest@example.invalid
export GIT_COMMITTER_NAME=selftest GIT_COMMITTER_EMAIL=selftest@example.invalid

token="$(python3 -c 'import secrets, string
alphabet = string.ascii_letters + string.digits
print("ghp" + "_" + "".join(secrets.choice(alphabet) for _ in range(36)))')"

planted() { # <name> -> a new repository with one clean commit on main; prints its path
  local dir="$WORK/$1"
  git init -q -b main "$dir"
  printf 'clean\n' >"$dir/README"
  git -C "$dir" add README
  git -C "$dir" commit -q -m base
  echo "$dir"
}

merged() { # <repository> <file> <content>: a side branch merged into main, the merge adding <file>
  local dir="$1"
  git -C "$dir" checkout -q -b side
  printf 'side\n' >"$dir/side.txt"
  git -C "$dir" add side.txt
  git -C "$dir" commit -q -m side
  git -C "$dir" checkout -q main
  printf 'main\n' >"$dir/main.txt"
  git -C "$dir" add main.txt
  git -C "$dir" commit -q -m main
  git -C "$dir" merge -q --no-ff --no-commit side >/dev/null 2>&1
  printf '%s\n' "$3" >"$dir/$2"
  git -C "$dir" add "$2"
  git -C "$dir" commit -q -m merge
}

failures=0
expect() { # <found|clean> <case> <repository> [range]
  local want="$1" name="$2" code=0 verdict
  env SECRET_SCAN_ROOT="$3" SECRET_SCAN_RANGE="${4:-}" SECRET_REPORT="$WORK/$name.md" \
    bash "$HERE/secret-scan.sh" >"$WORK/$name.log" 2>&1 || code=$?
  if [[ "$code" == 1 ]] && grep -q '^| github-pat |' "$WORK/$name.md" 2>/dev/null; then
    verdict=found
  elif [[ "$code" == 1 ]] && grep -q '^::error::\.gitleaksignore is not honoured' "$WORK/$name.log"; then
    verdict=refused
  elif [[ "$code" == 0 ]] && grep -q '^No secrets found\.$' "$WORK/$name.log"; then
    verdict=clean
  else
    verdict="an error (exit ${code})"
  fi
  if [[ "$verdict" == "$want" ]]; then
    echo "ok: ${name}: ${verdict}"
  else
    echo "::error::Secret scan self-test, ${name}: expected ${want}, got ${verdict}." >&2
    cat "$WORK/$name.log" >&2
    failures=$((failures + 1))
  fi
}

# Clean, with a merge in its history.
clean="$(planted clean)"
merged "$clean" notes.txt "nothing secret"
expect clean "a clean repository with a merge" "$clean" --all

# A secret the merge commit itself introduces, removed by the next commit.
merge="$(planted merge)"
base="$(git -C "$merge" rev-parse HEAD)"
merged "$merge" config.env "TOKEN=${token}"
git -C "$merge" rm -q config.env
git -C "$merge" commit -q -m "remove it"
expect found "a secret introduced by a merge, in the range" "$merge" "${base}..HEAD"
expect found "a secret introduced by a merge, in the whole history" "$merge" --all

# A secret its own line marks as allowed.
inline="$(planted inline)"
printf 'TOKEN=%s # gitleaks:allow\n' "$token" >"$inline/config.env"
git -C "$inline" add config.env
git -C "$inline" commit -q -m inline
expect found "a secret marked gitleaks:allow, in the tree" "$inline"
expect found "a secret marked gitleaks:allow, in the range" "$inline" HEAD~1..HEAD

# A secret whose fingerprints a committed .gitleaksignore lists, for the tree and the commit.
ignored="$(planted ignored)"
printf 'TOKEN=%s\n' "$token" >"$ignored/config.env"
git -C "$ignored" add config.env
git -C "$ignored" commit -q -m ignored
printf 'config.env:github-pat:1\n%s:config.env:github-pat:1\n' "$(git -C "$ignored" rev-parse HEAD)" \
  >"$ignored/.gitleaksignore"
git -C "$ignored" add .gitleaksignore
git -C "$ignored" commit -q -m "ignore it"
expect refused "a secret listed in .gitleaksignore, in the tree" "$ignored"
expect refused "a secret listed in .gitleaksignore, in the range" "$ignored" HEAD~2..HEAD
# The same secret with the file gone from the tree but still in the history is found.
git -C "$ignored" rm -q .gitleaksignore
git -C "$ignored" commit -q -m "drop the ignore file"
expect found "a secret once listed in .gitleaksignore, in the range" "$ignored" HEAD~3..HEAD

if [[ "$failures" != 0 ]]; then
  echo "::error::The secret scan self-test failed ${failures} case(s)." >&2
  exit 1
fi
echo "The secret scan finds every planted secret and passes the clean repository."
