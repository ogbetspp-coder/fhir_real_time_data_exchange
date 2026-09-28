#!/usr/bin/env bash
# Known-vulnerability scan (docs/foundations.md, C1), with Google's open-source OSV-Scanner.
#
# Registry scanning in Artifact Analysis is charged per image pushed, and at this project's deploy
# rate it would cost most of the monthly budget; it is the production choice, where deploys are
# rare. This scan is the development one: free, cloud-agnostic, and it runs before merge.
#
# Four parts, with different verdicts:
#   - the lockfiles (npm, and uv for the agent and Zone A): any known vulnerability FAILS. They
#     have none today, and a fix for a library is a lockfile bump away.
#   - the pinned base images the Dockerfiles build on: REPORTED, never failing. Operating-system
#     advisories in a Debian or Ubuntu base mostly have no fixed package yet, and the fix when
#     there is one arrives as a digest bump from Dependabot. The report makes them visible.
#   - the official validator's jar, at its pin in Dockerfile.validator and checked against its
#     SHA-256: REPORTED (audit B07, S-2). It parses submitted content in the deployed sidecar, and
#     nothing scanned it. It is a fat jar that keeps the Maven metadata of almost none of the
#     libraries it bundles, so the scan identifies few of them; the report says how many.
#   - the Agent Engine runtime's requirements, generated as the deploy generates them (uv.lock's
#     runtime export plus the SDK pins of agent/deploy/deploy_agent_engine.py) and resolved for
#     Google's build platform: REPORTED (audit B07, S-2). They are resolved when the scan runs,
#     not locked, so a failing verdict would depend on the day. Needs uv; skipped without it.
#
# The scanner is downloaded at a pinned version and verified against a pinned SHA-256, like every
# other third-party dependency in CI. An exception to the lockfile verdict goes in the root
# osv-scanner.toml with a reason and an expiry, in a reviewed pull request; no other is read.
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
# Always an explicit --config: without one, OSV-Scanner loads an osv-scanner.toml it finds beside
# any lockfile it scans, so a file dropped next to agent/uv.lock could ignore advisories without
# touching the reviewed root one. With no root file, an empty one stands in and ignores nothing.
config="$ROOT/osv-scanner.toml"
if [[ ! -f "$config" ]]; then
  config="$WORK/osv-scanner.toml"
  : >"$config"
fi

# 1. Lockfiles: the verdict.
# VULN_LOCKFILES overrides the list, so the verdict itself can be proved on a known-bad lockfile.
read -r -a lockfiles <<<"${VULN_LOCKFILES:-$ROOT/package-lock.json $ROOT/agent/uv.lock $ROOT/zone-a/uv.lock}"
lock_args=()
for lockfile in "${lockfiles[@]}"; do lock_args+=(-L "$lockfile"); done
"$WORK/osv-scanner" scan source --config "$config" --format json "${lock_args[@]}" \
  >"$WORK/locks.json" 2>"$WORK/locks.err" || true
if ! python3 -c "import json,sys;json.load(open(sys.argv[1]))" "$WORK/locks.json" 2>/dev/null; then
  cat "$WORK/locks.err" >&2
  echo "The lockfile scan did not produce a result." >&2
  exit 1
fi
lock_findings="$(summarise "$WORK/locks.json" "Lockfiles (npm, agent, Zone A)" 3>&1 1>>"$REPORT")"

# 2. Base images: reported only. Every FROM of every Dockerfile, the runtime bases included.
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

# 3. The validator jar: reported only. Downloaded at the pin Dockerfile.validator names and
# verified against its SHA-256 before it is scanned.
read -r jar_url jar_sha < <(cd "$ROOT" && node --input-type=module -e '
  import { readSidecarPins } from "./scripts/ci/validator-pins.mjs";
  const jar = readSidecarPins("Dockerfile.validator").artefacts.find(({ file }) => file === "validator_cli.jar");
  console.log(jar.url, jar.sha256);
' 2>/dev/null || true)
if [[ -n "${jar_url:-}" && "${OSV_SKIP_JAR:-false}" != "true" ]]; then
  mkdir -p "$WORK/jar"
  if curl -fsSL --retry 3 -o "$WORK/jar/validator_cli.jar" "$jar_url" &&
    { echo "${jar_sha}  ${WORK}/jar/validator_cli.jar" | sha256sum -c - >/dev/null 2>&1 ||
      echo "${jar_sha}  ${WORK}/jar/validator_cli.jar" | shasum -a 256 -c - >/dev/null 2>&1; }; then
    "$WORK/osv-scanner" scan source --config "$config" --format json \
      --experimental-plugins java/archive -r "$WORK/jar" >"$WORK/jar.json" 2>"$WORK/jar.err" || true
    identified="$(grep -oE 'found [0-9]+ packages' "$WORK/jar.err" | grep -oE '[0-9]+' | head -1)"
    if python3 -c "import json,sys;json.load(open(sys.argv[1]))" "$WORK/jar.json" 2>/dev/null; then
      summarise "$WORK/jar.json" "Validator jar ${jar_url##*/download/} (reported, not failing; ${identified:-no} bundled packages identifiable)" 3>/dev/null >>"$REPORT"
    else
      printf '#### Validator jar: scan did not complete (reported, not failing)\n\n' >>"$REPORT"
    fi
  else
    printf '#### Validator jar: download or checksum failed (reported, not failing)\n\n' >>"$REPORT"
  fi
else
  printf '_Validator jar not scanned here (its pin could not be read, or OSV_SKIP_JAR)._\n\n' >>"$REPORT"
fi

# 4. The Agent Engine runtime's requirements: reported only.
uv_bin="${UV:-$(command -v uv || true)}"
if [[ -n "$uv_bin" && "${OSV_SKIP_AGENT:-false}" != "true" ]]; then
  if "$uv_bin" --directory "$ROOT/agent" export --frozen --no-dev --no-emit-project --no-hashes \
    --all-extras --format requirements-txt >"$WORK/agent-lock.txt" 2>"$WORK/agent.err" &&
    grep -oE '"(google-cloud-agentplatform|google-cloud-aiplatform)\[[^]]*\]==[^"]+"|"cloudpickle==[^"]+"' \
      "$ROOT/agent/deploy/deploy_agent_engine.py" | tr -d '"' >"$WORK/agent-sdk.txt" &&
    [[ "$(wc -l <"$WORK/agent-sdk.txt")" -eq 3 ]] &&
    cat "$WORK/agent-lock.txt" "$WORK/agent-sdk.txt" | "$uv_bin" pip compile - --python-version 3.14 \
      --python-platform x86_64-manylinux_2_28 --no-header --quiet \
      -o "$WORK/agent-requirements.txt" 2>>"$WORK/agent.err"; then
    "$WORK/osv-scanner" scan source --config "$config" --format json \
      -L "requirements.txt:$WORK/agent-requirements.txt" >"$WORK/agent.json" 2>>"$WORK/agent.err" || true
    if python3 -c "import json,sys;json.load(open(sys.argv[1]))" "$WORK/agent.json" 2>/dev/null; then
      summarise "$WORK/agent.json" "Agent Engine runtime requirements, $(grep -cE '^[a-z0-9]' "$WORK/agent-requirements.txt") packages resolved today (reported, not failing)" 3>/dev/null >>"$REPORT"
    else
      printf '#### Agent Engine runtime requirements: scan did not complete (reported, not failing)\n\n' >>"$REPORT"
    fi
  else
    printf '#### Agent Engine runtime requirements: could not be generated (reported, not failing)\n\n' >>"$REPORT"
  fi
else
  printf '_Agent Engine runtime requirements not scanned here (no uv, or OSV_SKIP_AGENT)._\n\n' >>"$REPORT"
fi

cat "$REPORT"
if [[ "$lock_findings" != "0" ]]; then
  echo "::error::${lock_findings} known vulnerabilit(ies) in the lockfiles. Upgrade, or record a reviewed exception in the root osv-scanner.toml." >&2
  exit 1
fi
echo "No known vulnerabilities in the lockfiles."
