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

// What .gitleaks.toml may hold, statement by statement: `[extend]` once with `useDefault = true`
// and nothing else, then `[[allowlists]]` tables of the keys the narrow-exception rule uses. Any
// other table or key (disabledRules, [[rules]], a per-rule or global allowlist, commits,
// stopwords, another base config) is a problem. Returns the problems found.
const ALLOWLIST_KEYS = new Set([
  "description",
  "condition",
  "targetRules",
  "paths",
  "regexTarget",
  "regexes",
]);

function configProblems(text: string): string[] {
  const problems: string[] = [];
  let table: string | undefined;
  let extendSeen = 0;
  let keys = new Set<string>();
  for (const raw of text.split(/\r?\n/)) {
    const line = raw.trim();
    if (line === "" || line.startsWith("#")) continue;
    if (line.startsWith("[")) {
      table = line;
      keys = new Set();
      if (line === "[extend]") extendSeen += 1;
      else if (line !== "[[allowlists]]") problems.push(`table ${line}`);
      continue;
    }
    const key = /^([A-Za-z]+)\s*=/.exec(line)?.[1];
    if (key === undefined) {
      problems.push(`not a single-line key: ${line}`);
      continue;
    }
    if (keys.has(key)) problems.push(`${key} twice in ${table ?? "the top level"}`);
    keys.add(key);
    if (table === "[extend]") {
      if (line !== "useDefault = true") problems.push(`[extend] ${line}`);
    } else if (table === "[[allowlists]]") {
      if (!ALLOWLIST_KEYS.has(key)) problems.push(`[[allowlists]] ${key}`);
    } else if (table === undefined) {
      problems.push(`top-level ${key}`);
    }
  }
  if (extendSeen !== 1) problems.push(`[extend] ${extendSeen} times`);
  return problems;
}

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
      'expect found "a secret marked gitleaks:allow, in the tree" "$inline"',
      'expect refused "a secret listed in .gitleaksignore, in the tree" "$ignored"',
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

  it("holds nothing else that could weaken a rule: the config's whole shape is fixed", () => {
    expect(configProblems(config)).toEqual([]);
    // Each way a gitleaks config can switch off or narrow a default rule is refused.
    for (const [weakening, text] of [
      ["a disabled rule", "[extend]\nuseDefault = true\ndisabledRules = ['github-pat']\n"],
      ["a default rule redefined", '[extend]\nuseDefault = true\n[[rules]]\nid = "github-pat"\n'],
      [
        "a per-rule allowlist",
        "[extend]\nuseDefault = true\n[[rules.allowlists]]\npaths = ['''.*''']\n",
      ],
      [
        "the older per-rule form",
        "[extend]\nuseDefault = true\n[rules.allowlist]\npaths = ['''.*''']\n",
      ],
      ["a global allowlist", "[extend]\nuseDefault = true\n[allowlist]\npaths = ['''.*''']\n"],
      [
        "an allowlist by commit",
        "[extend]\nuseDefault = true\n[[allowlists]]\ncommits = ['abc']\n",
      ],
      ["a stopword", "[extend]\nuseDefault = true\n[[allowlists]]\nstopwords = ['ghp']\n"],
      ["another base config", "[extend]\nuseDefault = true\npath = 'other.toml'\n"],
      ["no defaults", "[extend]\nuseDefault = false\n"],
      ["a top-level key", 'title = "x"\n[extend]\nuseDefault = true\n'],
    ] as const) {
      expect([weakening, configProblems(text).length > 0]).toEqual([weakening, true]);
    }
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
