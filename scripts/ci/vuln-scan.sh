#!/usr/bin/env bash
# Known-vulnerability scan (docs/foundations.md, C1), with Google's open-source OSV-Scanner.
#
# Registry scanning in Artifact Analysis is charged per image pushed, and at this project's deploy
# rate it would cost most of the monthly budget; it is the production choice, where deploys are
# rare. This scan is the development one: free, cloud-agnostic, and it runs before merge.
#
# Two parts, with different verdicts:
#   - the lockfiles (npm, and uv for the agent and Zone A): any known vulnerability FAILS. They
#     have none today, and a fix for a library is a lockfile bump away.
#   - the pinned base images the Dockerfiles build on: REPORTED, never failing. Operating-system
#     advisories in a Debian or Ubuntu base mostly have no fixed package yet, and the fix when
#     there is one arrives as a digest bump from Dependabot. The report makes them visible.
#
# The scanner is downloaded at a pinned version and verified against a pinned SHA-256, like every
# other third-party dependency in CI. An exception to the lockfile verdict goes in
# osv-scanner.toml with a reason and an expiry, in a reviewed pull request.
#
#   bash scripts/ci/vuln-scan.sh             # lockfiles, then images if docker is available
#   OSV_SKIP_IMAGES=true bash scripts/ci/vuln-scan.sh
set -euo pipefail

VERSION="v2.6.0"
case "$(uname -s)-$(uname -m)" in
  Linux-x86_64) ASSET="osv-scanner_linux_amd64"; SHA="ca69b3d3cd08f889a49dc0a383122f71cc528b83803671df5fd874d97485b108" ;;
  Darwin-arm64) ASSET="osv-scanner_darwin_arm64"; SHA="98c460dcd37de25819babd757d04542045b6243113e209edcd4d89fedb0256b4" ;;
  *) echo "No pinned OSV-Scanner build for $(uname -s)-$(uname -m)." >&2; exit 1 ;;
esac
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT
REPORT="${VULN_REPORT:-$WORK/report.md}"

curl -fsSL --retry 3 -o "$WORK/osv-scanner" \
  "https://github.com/google/osv-scanner/releases/download/${VERSION}/${ASSET}"
echo "${SHA}  ${WORK}/osv-scanner" | sha256sum -c - 2>/dev/null ||
  echo "${SHA}  ${WORK}/osv-scanner" | shasum -a 256 -c -
chmod +x "$WORK/osv-scanner"

summarise() { # <json> <title> -> markdown table on stdout; prints the finding count on fd 3
  python3 - "$1" "$2" 3>&3 <<'PY'
import json, os, sys
data = json.load(open(sys.argv[1]))
rows = []
for result in data.get("results", []):
    for package in result.get("packages", []):
        info = package["package"]
        for group in package.get("groups", []):
            rows.append((info["name"], info.get("version", ""), group.get("max_severity") or "?",
                         ", ".join(group["ids"][:3])))
print(f"#### {sys.argv[2]}: {len(rows)} known vulnerabilit{'y' if len(rows) == 1 else 'ies'}\n")
if rows:
    print("| Package | Version | Max severity | Advisories |\n|---|---|---|---|")
    for row in sorted(rows, key=lambda r: -float(r[2]) if r[2].replace('.', '', 1).isdigit() else 0):
        print("| " + " | ".join(row) + " |")
print()
os.write(3, f"{len(rows)}\n".encode())
PY
}

: >"$REPORT"
config=()
[[ -f "$ROOT/osv-scanner.toml" ]] && config=(--config "$ROOT/osv-scanner.toml")

# 1. Lockfiles: the verdict.
# VULN_LOCKFILES overrides the list, so the verdict itself can be proved on a known-bad lockfile.
read -r -a lockfiles <<<"${VULN_LOCKFILES:-$ROOT/package-lock.json $ROOT/agent/uv.lock $ROOT/zone-a/uv.lock}"
lock_args=()
for lockfile in "${lockfiles[@]}"; do lock_args+=(-L "$lockfile"); done
"$WORK/osv-scanner" scan source ${config[@]+"${config[@]}"} --format json "${lock_args[@]}" \
  >"$WORK/locks.json" 2>"$WORK/locks.err" || true
if ! python3 -c "import json,sys;json.load(open(sys.argv[1]))" "$WORK/locks.json" 2>/dev/null; then
  cat "$WORK/locks.err" >&2
  echo "The lockfile scan did not produce a result." >&2
  exit 1
fi
lock_findings="$(summarise "$WORK/locks.json" "Lockfiles (npm, agent, Zone A)" 3>&1 1>>"$REPORT")"

# 2. Base images: reported only.
if [[ "${OSV_SKIP_IMAGES:-false}" != "true" ]] && command -v docker >/dev/null 2>&1; then
  while read -r image; do
    "$WORK/osv-scanner" scan image --format json "$image" >"$WORK/image.json" 2>"$WORK/image.err" || true
    if python3 -c "import json,sys;json.load(open(sys.argv[1]))" "$WORK/image.json" 2>/dev/null; then
      summarise "$WORK/image.json" "Base image ${image%%@*} (reported, not failing)" 3>/dev/null >>"$REPORT"
    else
      printf '#### Base image %s: scan did not complete (reported, not failing)\n\n' "${image%%@*}" >>"$REPORT"
    fi
  done < <(grep -hoE '^FROM [^ ]+@sha256:[0-9a-f]{64}' "$ROOT"/Dockerfile* | cut -d' ' -f2 | sort -u)
else
  printf '_Base images not scanned here (no docker, or OSV_SKIP_IMAGES)._\n\n' >>"$REPORT"
fi

cat "$REPORT"
if [[ "$lock_findings" != "0" ]]; then
  echo "::error::${lock_findings} known vulnerabilit(ies) in the lockfiles. Upgrade, or record a reviewed exception in osv-scanner.toml." >&2
  exit 1
fi
echo "No known vulnerabilities in the lockfiles."
