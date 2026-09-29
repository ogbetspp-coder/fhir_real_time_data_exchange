import { execFileSync } from "node:child_process";
import { readFileSync } from "node:fs";

import { describe, expect, it } from "vitest";

// The two scanners CI downloads and runs — OSV-Scanner (vulnerabilities, foundations C1) and
// gitleaks (secrets, foundations D4) — are pinned by version and SHA-256, and each binary is
// verified before it is executed. A moved release or a swapped asset fails the checksum rather
// than running unreviewed code in a required check. Moving a pin is a reviewed edit here and in
// the script together.

const scanners = [
  {
    script: "scripts/ci/vuln-scan.sh",
    version: "v2.6.0",
    url: "https://github.com/google/osv-scanner/releases/download/${VERSION}/${ASSET}",
    binary: '"$WORK/osv-scanner"',
    download: '"$WORK/osv-scanner"',
    sha: {
      "Linux-x86_64": "ca69b3d3cd08f889a49dc0a383122f71cc528b83803671df5fd874d97485b108",
      "Darwin-arm64": "98c460dcd37de25819babd757d04542045b6243113e209edcd4d89fedb0256b4",
    },
  },
  {
    script: "scripts/ci/secret-scan.sh",
    version: "8.30.1",
    url: "https://github.com/gitleaks/gitleaks/releases/download/v${VERSION}/${ASSET}",
    binary: '"$WORK/gitleaks"',
    download: '"$WORK/gitleaks.tar.gz"',
    sha: {
      "Linux-x86_64": "551f6fc83ea457d62a0d98237cbad105af8d557003051f41f3e7ca7b3f2470eb",
      "Darwin-arm64": "b40ab0ae55c505963e365f271a8d3846efbc170aa17f2607f13df610a9aeb6a5",
    },
  },
];

describe.each(scanners)("$script", ({ script, version, url, binary, download, sha }) => {
  const text = readFileSync(script, "utf8");

  it("pins the version", () => {
    expect(/^VERSION="([^"]+)"$/m.exec(text)?.[1]).toBe(version);
  });

  it("pins the SHA-256 of every platform build it will run, and refuses any other platform", () => {
    for (const [platform, digest] of Object.entries(sha)) {
      const line = new RegExp(`^\\s*${platform}\\) ASSET="[^"]+"; SHA="([0-9a-f]{64})" ;;$`, "m");
      expect([platform, line.exec(text)?.[1]]).toEqual([platform, digest]);
    }
    expect(text).toMatch(/^\s*\*\) echo "No pinned .+ build for .+" >&2; exit 1 ;;$/m);
  });

  it("downloads from the project's own release, and verifies before it executes", () => {
    expect(text).toContain(url);
    const verify = text.indexOf(`echo "\${SHA}  \${WORK}/`);
    const firstRun = text.indexOf(`${binary} `, text.indexOf("chmod +x"));
    expect(text.indexOf(`-o ${download}`)).toBeGreaterThan(-1);
    expect(verify).toBeGreaterThan(text.indexOf(`-o ${download}`));
    expect(text.indexOf("chmod +x")).toBeGreaterThan(verify);
    expect(firstRun).toBeGreaterThan(text.indexOf("chmod +x"));
    expect(text).toMatch(/sha256sum -c - 2>\/dev\/null \|\|\n\s+echo .+ \| shasum -a 256 -c -/);
    expect(text).toMatch(/^set -euo pipefail$/m);
  });
});

describe("the secret scan in CI", () => {
  const workflow = readFileSync(".github/workflows/vulnerabilities.yml", "utf8");
  const config = readFileSync(".gitleaks.toml", "utf8");

  it("runs in the required Vulnerabilities check, on the commits added, even after a failed scan", () => {
    expect(workflow).toContain("name: Vulnerabilities");
    expect(workflow).toMatch(
      /- name: Scan for secrets\n\s+if: success\(\) \|\| failure\(\)\n[\s\S]*?SECRET_SCAN_RANGE="\$range" bash scripts\/ci\/secret-scan\.sh/,
    );
    expect(workflow).toContain('pull_request) range="${PR_BASE}..${PR_HEAD}" ;;');
    expect(workflow).toContain("fetch-depth: 0");
  });

  it("proves the scan on planted secrets before it trusts a clean one", () => {
    const proof = workflow.indexOf(
      "- name: Prove the secret scan on planted secrets\n        if: success() || failure()\n        run: bash scripts/ci/secret-scan-selftest.sh\n",
    );
    expect(proof).toBeGreaterThan(-1);
    expect(proof).toBeLessThan(workflow.indexOf("- name: Scan for secrets"));
    const selftest = readFileSync("scripts/ci/secret-scan-selftest.sh", "utf8");
    // Each place the scan once missed a secret, and the clean control.
    for (const planted of [
      'expect clean "a clean repository with a merge" "$clean" --all',
      'expect found "a secret introduced by a merge, in the range" "$merge" "${base}..HEAD"',
      'expect found "a secret introduced by a merge, in the whole history" "$merge" --all',
      'expect found "a secret in a -diff file, in the range" "$attribute" HEAD~2..HEAD',
      'expect found "a secret in a file with a NUL byte, in the range" "$nul" HEAD~2..HEAD',
      "for file in creds.bin sub/package-lock.json; do",
      'expect found "a secret in ${file}, in the tree" "$named"',
      'expect found "a secret in ${file}, in the range" "$named" HEAD~1..HEAD',
      'expect found "a secret in a commit message, in the range" "$message" HEAD~1..HEAD',
    ]) {
      expect(selftest).toContain(planted);
    }
    // The planted token is built at run time; the file carries none of its own.
    expect(selftest).not.toMatch(/ghp_[0-9A-Za-z]{36}/);
  });

  it("honours no exception but .gitleaks.toml, and reads merge commits", () => {
    const script = readFileSync("scripts/ci/secret-scan.sh", "utf8");
    const common = /^common=\(([^)]*)\)$/m.exec(script)?.[1] ?? "";
    expect(common).toContain('--config "$CONFIG"');
    expect(common).toContain("--ignore-gitleaks-allow");
    expect(common).toContain('--gitleaks-ignore-path "$WORK/no-ignore-file"');
    expect(script).toContain('mkdir "$WORK/no-ignore-file"');
    // gitleaks reads the scanned root's .gitleaksignore whatever the flag says: refused outright.
    expect(script).toMatch(/if \[\[ -e "\$ROOT\/\.gitleaksignore" \]\]; then\n.*\n\s+exit 1\n/);
    expect(script).toContain('git --log-opts="-m --text ${SECRET_SCAN_RANGE}" "$ROOT"');
    expect(script.match(/--log-opts=/g)).toHaveLength(1);
    // The same commits' messages, through the same configuration and flags.
    expect(script).toContain(
      'git -C "$ROOT" log --format=\'commit %H%n%B\' ${SECRET_SCAN_RANGE} >"$WORK/messages.txt"',
    );
    expect(script).toContain('--messages "$WORK/messages.txt" stdin 3>&1');
    expect(script).toMatch(/"\$message_findings" != "0"/);
    const tracked = execFileSync(
      "git",
      ["ls-files", "--", ".gitleaksignore", "**/.gitleaksignore"],
      {
        encoding: "utf8",
      },
    );
    expect(tracked).toBe("");
  });

  it("lets only a weekly, checkout-free job write issues", () => {
    const head = workflow.slice(0, workflow.indexOf("\njobs:\n"));
    expect(head).toMatch(/^permissions:\n {2}contents: read\n(?! )/m);
    const jobs = workflow.slice(workflow.indexOf("\njobs:\n")).split(/\n(?= {2}[a-z-]+:\n)/);
    const writers = jobs.filter((job) => job.includes("issues: write"));
    expect(writers).toHaveLength(1);
    const [writer = ""] = writers;
    expect(writer).toMatch(/^ {4}if: failure\(\) && github\.event_name == 'schedule'$/m);
    expect(writer).toMatch(/^ {4}needs: scan$/m);
    expect(writer).not.toContain("actions/checkout");
    expect(writer).not.toMatch(/\bbash scripts\//);
  });

  it("applies gitleaks' pinned default rules to every file, without its global path exemptions", () => {
    const script = readFileSync("scripts/ci/secret-scan.sh", "utf8");
    expect(script).toContain(
      'DEFAULT_CONFIG_URL="https://raw.githubusercontent.com/gitleaks/gitleaks/v${VERSION}/config/gitleaks.toml"',
    );
    expect(script).toMatch(/^DEFAULT_CONFIG_SHA="e163e53b9e7e8a85[0-9a-f]{48}"$/m);
    // Downloaded, verified, then turned into the configuration the scan passes.
    const download = script.indexOf('-o "$WORK/default.toml" "$DEFAULT_CONFIG_URL"');
    const verify = script.indexOf('echo "${DEFAULT_CONFIG_SHA}  ${WORK}/default.toml" | sha256sum');
    const build = script.indexOf('python3 - "$WORK/default.toml" "$EXCEPTIONS" "$CONFIG"');
    expect(download).toBeGreaterThan(-1);
    expect(verify).toBeGreaterThan(download);
    expect(build).toBeGreaterThan(verify);
    expect(script.indexOf('CONFIG="$WORK/config.toml"')).toBeLessThan(build);
    expect(script.indexOf("common=(--no-banner")).toBeGreaterThan(build);
    // The global allowlist's paths are dropped, and a failure to drop them stops the scan.
    expect(script).toContain(`re.subn(r"^paths = \\[\\n(?:    '''.*''',\\n)+\\]\\n", "", table`);
    expect(script).toContain('if dropped != 1 or re.search(r"^paths\\b", table, re.M):');
  });

  it("holds exceptions that each need a rule, a path and a pattern at once", () => {
    const exceptions = config.split(/^\[\[allowlists\]\]$/m).slice(1);
    expect(exceptions.length).toBeGreaterThan(0);
    for (const exception of exceptions) {
      expect(exception).toMatch(/^condition = "AND"$/m);
      expect(exception).toMatch(/^targetRules = \["[a-z-]+"\]$/m);
      expect(exception).toMatch(/^paths = \['''\^[^']+\$'''\]$/m);
      expect(exception).toMatch(/^regexes = \['''\^[^']+\$'''\]$/m);
    }
    // No global allowlist, which would exempt its paths or patterns from every rule.
    expect(config).not.toMatch(/^\[allowlist\]$/m);
  });
});

describe("the vulnerability scan's exceptions", () => {
  it("always names its config, so no osv-scanner.toml beside a lockfile is read", () => {
    const script = readFileSync("scripts/ci/vuln-scan.sh", "utf8");
    expect(script).toContain('"$WORK/osv-scanner" scan source --config "$config" ');
    expect(script).toMatch(/^config="\$ROOT\/osv-scanner\.toml"$/m);
    expect(script).toMatch(/^ {2}config="\$WORK\/osv-scanner\.toml"\n {2}: >"\$config"$/m);
  });
});
