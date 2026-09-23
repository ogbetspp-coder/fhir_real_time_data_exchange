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

  it("uses every default rule, and each exception needs a rule, a path and a pattern at once", () => {
    expect(config).toMatch(/^\[extend\]\nuseDefault = true$/m);
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
