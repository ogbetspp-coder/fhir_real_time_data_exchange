#!/usr/bin/env bash
# Secret scan (docs/foundations.md, D4), with the open-source gitleaks.
#
# GitHub's secret scanning and push protection need its paid secret protection on a private
# repository, so nothing stopped a credential from being committed. This scan is the free
# substitute, run on every pull request, on main and weekly by the Vulnerabilities workflow. It
# does not stop a push; it fails the required check, so a credential cannot merge unnoticed.
#
# Two parts, both failing on any finding:
#   - the working tree: every tracked file, and every untracked file git does not ignore, copied
#     out first so node_modules, .venv and build output are never read;
#   - the pushed commits: every commit in SECRET_SCAN_RANGE (a git revision range such as
#     base..head, or --all), so a secret added in one commit and removed in the next is still
#     found. Merge commits are read against each parent (git log -m): without it git prints no
#     patch for a merge, and a secret the merge itself introduced was never read. Every file is
#     read as text (git log --text): git prints no patch for one it takes as binary, a NUL byte
#     or a `-diff` attribute in .gitattributes being enough. Unset, only the working tree is
#     scanned.
#
# The scanner is downloaded at a pinned version and verified against a pinned SHA-256 before it
# runs, like OSV-Scanner in vuln-scan.sh. Findings are redacted: the report names the rule, the
# file, the line and the commit, never the matched text. An exception goes in .gitleaks.toml, as
# narrow as the known false positive it covers, in a reviewed pull request, and nowhere else:
# gitleaks' own escape hatches, an inline `gitleaks:allow` comment (ignored) and a .gitleaksignore
# file of fingerprints (refused), are both closed, since either lets the change that adds a secret
# exempt it.
#
# SECRET_SCAN_ROOT scans another repository with this one's .gitleaks.toml, so the verdict itself
# can be proved on planted secrets (scripts/ci/secret-scan-selftest.sh).
#
#   bash scripts/ci/secret-scan.sh
#   SECRET_SCAN_RANGE=origin/main..HEAD bash scripts/ci/secret-scan.sh
set -euo pipefail

VERSION="8.30.1"
case "$(uname -s)-$(uname -m)" in
  Linux-x86_64) ASSET="gitleaks_${VERSION}_linux_x64.tar.gz"; SHA="551f6fc83ea457d62a0d98237cbad105af8d557003051f41f3e7ca7b3f2470eb" ;;
  Darwin-arm64) ASSET="gitleaks_${VERSION}_darwin_arm64.tar.gz"; SHA="b40ab0ae55c505963e365f271a8d3846efbc170aa17f2607f13df610a9aeb6a5" ;;
  *) echo "No pinned gitleaks build for $(uname -s)-$(uname -m)." >&2; exit 1 ;;
esac
CONFIG="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)/.gitleaks.toml"
ROOT="$(cd "${SECRET_SCAN_ROOT:-$(dirname "${BASH_SOURCE[0]}")/../..}" && pwd)"
# gitleaks 8.30.1 reads a .gitleaksignore at the root of the directory or repository it scans
# whatever --gitleaks-ignore-path says (measured), so one there is refused, not silently honoured.
if [[ -e "$ROOT/.gitleaksignore" ]]; then
  echo "::error::.gitleaksignore is not honoured: remove it, and record a reviewed exception in .gitleaks.toml for a false positive." >&2
  exit 1
fi
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT
REPORT="${SECRET_REPORT:-$WORK/report.md}"

curl -fsSL --retry 3 -o "$WORK/gitleaks.tar.gz" \
  "https://github.com/gitleaks/gitleaks/releases/download/v${VERSION}/${ASSET}"
echo "${SHA}  ${WORK}/gitleaks.tar.gz" | sha256sum -c - 2>/dev/null ||
  echo "${SHA}  ${WORK}/gitleaks.tar.gz" | shasum -a 256 -c -
tar -xzf "$WORK/gitleaks.tar.gz" -C "$WORK" gitleaks
chmod +x "$WORK/gitleaks"

# --exit-code 3 separates "found something" from an error of the scanner itself (1), which must
# also fail the check rather than read as clean. --ignore-gitleaks-allow reports a line even when
# it carries `gitleaks:allow`; --gitleaks-ignore-path names an empty directory, so the working
# directory's .gitleaksignore is not read either (the scanned root's is refused above).
mkdir "$WORK/no-ignore-file"
common=(--no-banner --no-color --redact --exit-code 3 --config "$CONFIG" --report-format json
  --ignore-gitleaks-allow --gitleaks-ignore-path "$WORK/no-ignore-file")

summarise() { # <json> <title> -> markdown on stdout; prints the finding count on fd 3
  python3 - "$1" "$2" 3>&3 <<'PY'
import json, os, sys
data = json.load(open(sys.argv[1])) or []
print(f"#### {sys.argv[2]}: {len(data)} finding{'' if len(data) == 1 else 's'}\n")
if data:
    print("| Rule | File | Line | Commit |\n|---|---|---|---|")
    for f in data:
        print(f"| {f['RuleID']} | {f['File']} | {f['StartLine']} | {(f.get('Commit') or '')[:12]} |")
print()
os.write(3, f"{len(data)}\n".encode())
PY
}

scan() { # <title> <report json> <gitleaks args...>; appends to the report, returns the count on fd 3
  local title="$1" json="$2" code=0
  shift 2
  "$WORK/gitleaks" "$@" "${common[@]}" --report-path "$json" >"$WORK/scan.log" 2>&1 || code=$?
  if [[ "$code" != 0 && "$code" != 3 ]] || ! python3 -c "import json,sys;json.load(open(sys.argv[1]))" "$json" 2>/dev/null; then
    cat "$WORK/scan.log" >&2
    echo "::error::The secret scan (${title}) did not complete (gitleaks exit ${code})." >&2
    exit 1
  fi
  summarise "$json" "$title" 3>&3 >>"$REPORT"
}

: >"$REPORT"

# 1. The working tree, as git sees it, without anything git ignores.
tree="$WORK/tree"
mkdir -p "$tree"
git -C "$ROOT" ls-files -z --cached --others --exclude-standard | python3 -c '
import os, shutil, sys
root, dest = sys.argv[1], sys.argv[2]
for rel in filter(None, sys.stdin.buffer.read().split(b"\0")):
    rel = os.fsdecode(rel)
    src = os.path.join(root, rel)
    if os.path.isfile(src) and not os.path.islink(src):
        os.makedirs(os.path.dirname(os.path.join(dest, rel)), exist_ok=True)
        shutil.copyfile(src, os.path.join(dest, rel))
' "$ROOT" "$tree"
tree_findings="$(cd "$tree" && scan "Working tree" "$WORK/tree.json" dir . 3>&1 1>>"$REPORT")"

# 2. The commits being pushed or merged.
range_findings=0
if [[ -n "${SECRET_SCAN_RANGE:-}" ]]; then
  range_findings="$(scan "Commits ${SECRET_SCAN_RANGE}" "$WORK/range.json" git --log-opts="-m --text ${SECRET_SCAN_RANGE}" "$ROOT" 3>&1 1>>"$REPORT")"
else
  printf '_No commit range given (SECRET_SCAN_RANGE); the working tree only._\n\n' >>"$REPORT"
fi

cat "$REPORT"
if [[ "$tree_findings" != "0" || "$range_findings" != "0" ]]; then
  echo "::error::Possible secrets: ${tree_findings} in the working tree, ${range_findings} in the commits scanned. Remove and rotate a real one; record a reviewed exception in .gitleaks.toml for a false positive." >&2
  exit 1
fi
echo "No secrets found."
