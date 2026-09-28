#!/usr/bin/env bash
# Secret scan (docs/foundations.md, D4), with the open-source gitleaks.
#
# GitHub's secret scanning and push protection need its paid secret protection on a private
# repository, so nothing stopped a credential from being committed. This scan is the free
# substitute, run on every pull request, on main and weekly by the Vulnerabilities workflow. It
# does not stop a push; it fails the required check, so a credential cannot merge unnoticed.
#
# Two parts, each failing on any finding:
#   - the working tree: every tracked file, and every untracked file git does not ignore, copied
#     out first so node_modules, .venv and build output are never read;
#   - the pushed commits: every commit in SECRET_SCAN_RANGE (a git revision range such as
#     base..head, or --all), so a secret added in one commit and removed in the next is still
#     found. Merge commits are read against each parent (git log -m): without it git prints no
#     patch for a merge, and a secret the merge itself introduced was never read. Every file is
#     read as text (git log --text): git prints no patch for one it takes as binary, a NUL byte
#     or a `-diff` attribute in .gitattributes being enough. The same commits' messages are
#     scanned too. Unset, only the working tree is scanned.
#
# The scanner is downloaded at a pinned version and verified against a pinned SHA-256 before it
# runs, like OSV-Scanner in vuln-scan.sh. Findings are redacted: the report names the rule, the
# file, the line and the commit, never the matched text. An exception goes in .gitleaks.toml, as
# narrow as the known false positive it covers, in a reviewed pull request, and nowhere else:
# gitleaks' own escape hatches, an inline `gitleaks:allow` comment (ignored) and a .gitleaksignore
# file of fingerprints (refused), are both closed, since either lets the change that adds a secret
# exempt it.
#
# The rules are gitleaks' own default configuration for the pinned version, downloaded and
# verified against a pinned SHA-256 like the binary, with one change: its global allowlist's
# `paths` are dropped. They exempt whole files from every rule by name (*.bin, *.pdf, *.svg,
# package-lock.json, any path containing gitleaks.toml, bootstrap*.js and 20 more), which is
# exactly the kind of exception this repository makes only in .gitleaks.toml. Its global value
# patterns (`true`, `${VAR}` and the like, which are never credentials) and its per-rule
# allowlists (each one rule, one narrow path or line shape) are kept. .gitleaks.toml holds only
# this repository's [[allowlists]], appended to that configuration.
#
# Known limit: gitleaks matches bytes as UTF-8, so a token in a UTF-16 file (every other byte a
# NUL) is not found, in the tree or in the history. Nothing in this repository is UTF-16.
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
# gitleaks' default configuration at the same tag, and its SHA-256.
DEFAULT_CONFIG_URL="https://raw.githubusercontent.com/gitleaks/gitleaks/v${VERSION}/config/gitleaks.toml"
DEFAULT_CONFIG_SHA="e163e53b9e7e8a8511e77271e2b323ed057759542a6d988258afe3a1fa329caf"
EXCEPTIONS="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)/.gitleaks.toml"
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

# The configuration: the pinned default with its global path exemptions dropped, then this
# repository's exceptions. Any surprise in either file stops the scan rather than weakening it.
curl -fsSL --retry 3 -o "$WORK/default.toml" "$DEFAULT_CONFIG_URL"
echo "${DEFAULT_CONFIG_SHA}  ${WORK}/default.toml" | sha256sum -c - 2>/dev/null ||
  echo "${DEFAULT_CONFIG_SHA}  ${WORK}/default.toml" | shasum -a 256 -c -
CONFIG="$WORK/config.toml"
python3 - "$WORK/default.toml" "$EXCEPTIONS" "$CONFIG" <<'PY'
import re, sys
default, exceptions, out = (open(sys.argv[1]).read(), open(sys.argv[2]).read(), sys.argv[3])
# The one global allowlist, in the older single-table form, up to the next table header.
head, header, rest = default.partition("\n[allowlist]\n")
if not header or "\n[allowlist]\n" in rest or "\n[[allowlists]]\n" in default:
    sys.exit("gitleaks' default configuration no longer has exactly one [allowlist] table")
end = re.search(r"^\[", rest, re.M)
table, tail = rest[: end.start()], rest[end.start() :]
table, dropped = re.subn(r"^paths = \[\n(?:    '''.*''',\n)+\]\n", "", table, flags=re.M)
if dropped != 1 or re.search(r"^paths\b", table, re.M):
    sys.exit("could not drop the global allowlist's paths from gitleaks' default configuration")
# The repository's file holds [[allowlists]] only; anything that could extend, replace or switch
# off a rule is refused here as well as by test/ci/scanner-pins.test.ts.
for line in exceptions.splitlines():
    line = line.strip()
    if line.startswith("[") and line != "[[allowlists]]":
        sys.exit(f".gitleaks.toml may hold only [[allowlists]] tables, not {line}")
# [[allowlists]] rather than [allowlist]: gitleaks refuses a configuration that has both forms.
with open(out, "w") as f:
    f.write(f"{head}\n[[allowlists]]\n{table}{tail}\n# .gitleaks.toml\n{exceptions}")
PY

# --exit-code 3 separates "found something" from an error of the scanner itself (1), which must
# also fail the check rather than read as clean. --ignore-gitleaks-allow reports a line even when
# it carries `gitleaks:allow`; --gitleaks-ignore-path names an empty directory, so the working
# directory's .gitleaksignore is not read either (the scanned root's is refused above).
mkdir "$WORK/no-ignore-file"
common=(--no-banner --no-color --redact --exit-code 3 --config "$CONFIG" --report-format json
  --ignore-gitleaks-allow --gitleaks-ignore-path "$WORK/no-ignore-file")

summarise() { # <json> <title> [messages] -> markdown on stdout; prints the finding count on fd 3
  python3 - "$1" "$2" "${3:-}" 3>&3 <<'PY'
import json, os, re, sys
data = json.load(open(sys.argv[1])) or []
# A finding in the commit messages names the commit whose block holds its line.
messages = open(sys.argv[3], errors="replace").read().split("\n") if sys.argv[3] else []
def commit(finding):
    for line in reversed(messages[: max(finding["StartLine"], 1)]):
        if re.fullmatch(r"commit [0-9a-f]{40}", line):
            return line[7:19]
    return (finding.get("Commit") or "")[:12]
print(f"#### {sys.argv[2]}: {len(data)} finding{'' if len(data) == 1 else 's'}\n")
if data:
    print("| Rule | File | Line | Commit |\n|---|---|---|---|")
    for f in data:
        where = f["File"] or ("(commit message)" if messages else "")
        print(f"| {f['RuleID']} | {where} | {f['StartLine']} | {commit(f)} |")
print()
os.write(3, f"{len(data)}\n".encode())
PY
}

scan() { # <title> <report json> [--messages <file>] <gitleaks args...>; appends to the report,
  # returns the count on fd 3. With --messages, gitleaks reads that file on its standard input.
  local title="$1" json="$2" messages="" code=0
  shift 2
  if [[ "$1" == "--messages" ]]; then
    messages="$2"
    shift 2
  fi
  "$WORK/gitleaks" "$@" "${common[@]}" --report-path "$json" <"${messages:-/dev/null}" \
    >"$WORK/scan.log" 2>&1 || code=$?
  if [[ "$code" != 0 && "$code" != 3 ]] || ! python3 -c "import json,sys;json.load(open(sys.argv[1]))" "$json" 2>/dev/null; then
    cat "$WORK/scan.log" >&2
    echo "::error::The secret scan (${title}) did not complete (gitleaks exit ${code})." >&2
    exit 1
  fi
  summarise "$json" "$title" "$messages" 3>&3 >>"$REPORT"
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
message_findings=0
if [[ -n "${SECRET_SCAN_RANGE:-}" ]]; then
  range_findings="$(scan "Commits ${SECRET_SCAN_RANGE}" "$WORK/range.json" git --log-opts="-m --text ${SECRET_SCAN_RANGE}" "$ROOT" 3>&1 1>>"$REPORT")"
  # 3. The same commits' messages, which git log -p shows gitleaks no patch line of: each
  # message under a `commit <hash>` line, so a finding names its commit.
  # shellcheck disable=SC2086 # the range is git log arguments, split as --log-opts splits them
  git -C "$ROOT" log --format='commit %H%n%B' ${SECRET_SCAN_RANGE} >"$WORK/messages.txt"
  message_findings="$(scan "Commit messages ${SECRET_SCAN_RANGE}" "$WORK/messages.json" --messages "$WORK/messages.txt" stdin 3>&1 1>>"$REPORT")"
else
  printf '_No commit range given (SECRET_SCAN_RANGE); the working tree only._\n\n' >>"$REPORT"
fi

cat "$REPORT"
if [[ "$tree_findings" != "0" || "$range_findings" != "0" || "$message_findings" != "0" ]]; then
  echo "::error::Possible secrets: ${tree_findings} in the working tree, ${range_findings} in the commits scanned, ${message_findings} in their messages. Remove and rotate a real one; record a reviewed exception in .gitleaks.toml for a false positive." >&2
  exit 1
fi
echo "No secrets found."
