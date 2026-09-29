import { readdirSync, readFileSync } from "node:fs";
import path from "node:path";

import { describe, expect, it } from "vitest";

// Which job of the deploy workflow may become the deployer. A job granted `id-token: write` can
// ask GitHub for an OIDC token that Workload Identity Federation exchanges for the deployer
// service account, and every process in the job can ask, before any auth step has run. Until
// 2026-09-22 the whole workflow held the grant and the quality gate ran in the deploy job, so an
// install script or a test dependency could have minted the deployer. These tests keep the gate
// in a job that cannot ask, and the one job that can from running install scripts.

const workflow = readFileSync(".github/workflows/deploy.yml", "utf8");

function jobs(text: string): Map<string, string> {
  const body = text.slice(text.indexOf("\njobs:\n") + "\njobs:\n".length);
  const found = new Map<string, string>();
  const headers = [...body.matchAll(/^ {2}([a-z][a-z0-9-]*):\n/gm)];
  headers.forEach((header, index) => {
    const start = header.index + header[0].length;
    const end = headers[index + 1]?.index ?? body.length;
    found.set(header[1] ?? "", body.slice(start, end));
  });
  return found;
}

const byName = jobs(workflow);

describe("deploy workflow permissions", () => {
  it("grants nothing at the workflow level", () => {
    expect(workflow).toMatch(/^permissions: \{\}$/m);
  });

  it("lets exactly one job ask for an OIDC token: deploy", () => {
    const withToken = [...byName].filter(([, text]) => text.includes("id-token: write"));
    expect(withToken.map(([name]) => name)).toEqual(["deploy"]);
  });

  it("runs the quality gate only in jobs that cannot ask for a token", () => {
    const gates = [...byName].filter(([, text]) => /npm (run check|test)\b/.test(text));
    expect(gates.map(([name]) => name)).toEqual(["gate"]);
    for (const [, text] of gates) expect(text).not.toMatch(/id-token/);
  });

  it("deploys only after the gate, and installs nothing", () => {
    // Until audit B08 (D-1) the deploy job installed the tree (scripts off) because bootstrap.sh
    // ran tsx; the gate job now makes what needed it, and this job installs no package at all.
    const deploy = byName.get("deploy") ?? "";
    expect(deploy).toMatch(/^ {4}needs: gate$/m);
    expect(deploy).not.toMatch(/deploy\.sh deps|npm (ci|install|i)\b|npx\b/);
  });

  it("receives what the gate job made for it, with the manifest's hash by a separate channel", () => {
    const gate = byName.get("gate") ?? "";
    const deploy = byName.get("deploy") ?? "";
    expect(gate).toMatch(
      /^ {6}deploy_inputs_sha256: \$\{\{ steps\.deploy_inputs\.outputs\.sha256 \}\}$/m,
    );
    expect(gate).toContain('bash scripts/gcp/deploy-inputs.sh "${RUNNER_TEMP}/deploy-inputs"');
    expect(gate).toMatch(/uses: actions\/upload-artifact@[0-9a-f]{40} # v/);
    expect(deploy).toMatch(/uses: actions\/download-artifact@[0-9a-f]{40} # v/);
    expect(deploy).toContain(
      "DEPLOY_INPUTS_SHA256: ${{ needs.gate.outputs.deploy_inputs_sha256 }}",
    );
  });
});

// What the job holding the grant runs (audit B08, D-1), written out. Every process in it can ask for
// the deployer's token, so none may start an installed package or a package manager, and its Node
// scripts import Node's built-ins only. Until B08, bootstrap.sh ran `npm run standards:fetch` and
// tsx here. The lists are an allowlist: a new script or action in the deploy job is a reviewed edit
// here. An indirect start (eval, a loader, a path built at run time) is for review, not for this
// test (owner decision O3).
const PHASES = [
  "preflight",
  "init",
  "apis",
  "images",
  "apply",
  "record-readers",
  "bootstrap",
  "smoke",
  "query-smoke",
];
// deploy.sh (without its deps and inputs phases, which only a local `deploy.sh all` runs) and the
// scripts its phases start; each sources scripts/gcp/common.sh, which is read with them.
const SHELL_SCRIPTS = [
  "scripts/gcp/bootstrap.sh",
  "scripts/gcp/deploy.sh",
  "scripts/gcp/reconcile-fhir-stores.sh",
  "scripts/gcp/record-readers.sh",
];
const NODE_SCRIPTS = [
  "scripts/ci/workflow-runs.mjs",
  "scripts/fhir/deploy-inputs.mjs",
  "scripts/fhir/fetch-standards.mjs",
  "scripts/fhir/select-import-resources.mjs",
];
// Every module those scripts load: each imports only Node's built-ins and the others here.
const NODE_MODULES = [...NODE_SCRIPTS, "scripts/fhir/standards.mjs"];
const PYTHON_SCRIPTS = [
  "scripts/ci/dashboard-drift.py",
  "scripts/ci/effective-iam.py",
  "scripts/ci/plan-summary.py",
  "scripts/ci/redact.py",
];
const ACTIONS = [
  "actions/checkout",
  "actions/setup-node",
  "actions/download-artifact",
  "google-github-actions/auth",
  "google-github-actions/setup-gcloud",
  "hashicorp/setup-terraform",
  "google-github-actions/auth",
  "google-github-actions/auth",
];

const RUNS_A_PACKAGE =
  /\btsx\b|node_modules\/\.bin|\bnpm (run|ci|install|i|exec|x|test)\b|\bnpx\b|\bpip3?\b|-m pip\b|\buvx?\b/;

const deployJob = byName.get("deploy") ?? "";
// The shell a text runs: without comment lines, and without the Python its heredocs feed python3.
const code = (text: string) =>
  text
    .replace(/<<'(\w+)'[^\n]*\n[\s\S]*?^\1$/gm, "")
    .split("\n")
    .filter((line) => !/^\s*#/.test(line))
    .join("\n");
const runs = new Map([
  ["deploy.yml (the deploy job)", code(deployJob)],
  ...[...SHELL_SCRIPTS, "scripts/gcp/common.sh"].map((file): [string, string] => [
    file,
    code(readFileSync(file, "utf8")).replace(/^phase_(?:deps|inputs)\(\) \{\n[\s\S]*?^\}$/gm, ""),
  ]),
]);
// Every script path started by `pattern`'s interpreter anywhere in what the job runs.
const started = (pattern: RegExp) =>
  [...new Set([...runs.values()].flatMap((text) => [...text.matchAll(pattern)].map((m) => m[1])))]
    .filter((file) => file !== undefined)
    .sort();

describe("the job that can become the deployer", () => {
  it("runs the deploy.sh phases it names, and no other", () => {
    const phases = [...deployJob.matchAll(/bash scripts\/gcp\/deploy\.sh ([a-z-]+)/g)];
    expect(phases.map((m) => m[1])).toEqual(PHASES);
  });

  it("starts only the scripts it names", () => {
    expect(started(/\bbash (scripts\/[\w/.-]+)/g)).toEqual(SHELL_SCRIPTS);
    expect(started(/\bnode (scripts\/[\w/.-]+)/g)).toEqual(NODE_SCRIPTS);
    expect(started(/\bpython3 (scripts\/[\w/.-]+)/g)).toEqual(PYTHON_SCRIPTS);
  });

  it("starts no installed package and no package manager", () => {
    for (const [where, text] of runs) {
      expect([where, RUNS_A_PACKAGE.exec(text)?.[0]]).toEqual([where, undefined]);
    }
  });

  it("starts Node scripts that import Node's built-ins and each other only", () => {
    for (const file of NODE_MODULES) {
      const outside = [
        ...readFileSync(file, "utf8").matchAll(
          /\bfrom\s+["']([^"']+)["']|\bimport\s*\(?\s*["']([^"']+)["']|\brequire\s*\(\s*["']([^"']+)["']/g,
        ),
      ]
        .map((m) => m[1] ?? m[2] ?? m[3] ?? "")
        .filter(
          (s) => !s.startsWith("node:") && !NODE_MODULES.includes(path.join(path.dirname(file), s)),
        );
      expect([file, outside]).toEqual([file, []]);
    }
  });

  it("uses only the actions it names, pinned, and checks its inputs before any credential", () => {
    const uses = [...deployJob.matchAll(/^\s*-?\s*uses:\s*(\S+)/gm)].map((m) => m[1] ?? "");
    expect(uses.map((use) => /^([\w./-]+)@[0-9a-f]{40}$/.exec(use)?.[1] ?? use)).toEqual(ACTIONS);
    const at = (text: string) => deployJob.indexOf(text);
    expect(at("node scripts/fhir/deploy-inputs.mjs verify")).toBeGreaterThan(
      at("uses: actions/download-artifact@"),
    );
    expect(at("node scripts/fhir/deploy-inputs.mjs verify")).toBeLessThan(
      at("uses: google-github-actions/auth@"),
    );
  });
});

// The same rule, for every workflow in the repository rather than the deploy alone. A job that
// installs the dependency tree with its scripts, or runs the tests and tools in it, must not be
// able to ask for an OIDC token; and no workflow may hand the grant to all of its jobs at once,
// because a job added later would inherit it without anyone deciding it should. The one
// installation allowed where the grant is held is the deploy's, and only with install scripts
// off (asserted for every workflow, so a second one cannot appear without the same guard).
const WORKFLOW_DIR = ".github/workflows";
const workflows = readdirSync(WORKFLOW_DIR)
  .filter((name) => /\.ya?ml$/.test(name))
  .sort()
  .map((name) => ({ name, text: readFileSync(`${WORKFLOW_DIR}/${name}`, "utf8") }));

// The top-level `permissions:` of a workflow: the line itself plus its indented block, or
// undefined when the workflow sets none (every job then gets the repository default).
function workflowPermissions(text: string): string | undefined {
  const head = text.slice(0, text.indexOf("\njobs:\n") + 1);
  return /^permissions:.*\n(?:(?: {2}.*)?\n)*/m.exec(head)?.[0];
}

// A step or job that runs the dependency tree: installs it (with whatever install scripts it
// carries) or executes its tests, linters and build tools.
const RUNS_THE_TREE = /\bnpm (ci|install|i|test|run check)\b|\bnpx\b|deploy\.sh deps\b/;
const INSTALLS = /\bnpm (ci|install|i)\b|deploy\.sh deps\b/;

describe("every workflow's permissions", () => {
  it("finds the workflows it is about", () => {
    expect(workflows.map(({ name }) => name)).toEqual(
      expect.arrayContaining(["ci.yml", "deploy.yml", "plan.yml", "vulnerabilities.yml"]),
    );
  });

  it.each(workflows)(
    "$name grants no OIDC token, and nothing write-all, at the workflow level",
    ({ text }) => {
      const permissions = workflowPermissions(text);
      // Absent is refused too: the repository default could then be write-all for every job.
      expect(permissions).toBeDefined();
      expect(permissions).not.toMatch(/id-token/);
      expect(permissions).not.toMatch(/write-all/);
    },
  );

  it.each(workflows)("$name grants write-all to no job", ({ text }) => {
    for (const [job, body] of jobs(text)) {
      expect([job, /^ {4}permissions: write-all/m.test(body)]).toEqual([job, false]);
    }
  });

  it.each(workflows)(
    "$name lets no job that installs, tests or checks hold id-token: write",
    ({ text }) => {
      for (const [job, body] of jobs(text)) {
        if (!body.includes("id-token: write")) continue;
        // The grant holder may install only through deploy.sh deps with install scripts off, and
        // may run nothing else from the tree.
        const direct = /\bnpm (ci|install|i|test|run check)\b|\bnpx\b/.exec(body);
        expect([job, direct?.[0]]).toEqual([job, undefined]);
      }
    },
  );

  it.each(workflows)(
    "$name installs with install scripts off wherever the job can ask for a token",
    ({ text }) => {
      for (const [job, body] of jobs(text)) {
        if (!body.includes("id-token: write")) continue;
        const steps = body.split(/\n(?= {6}- )/);
        for (const step of steps.filter((candidate) => INSTALLS.test(candidate))) {
          expect([job, step]).toEqual([
            job,
            expect.stringMatching(/npm_config_ignore_scripts: "true"/),
          ]);
        }
      }
    },
  );

  it("holds the rule against the jobs it exists for", () => {
    // The ones that run the tree, found by the same pattern the rule uses: if the pattern ever
    // stopped matching them, the rules above would pass for having nothing to check.
    const running = workflows.flatMap(({ name, text }) =>
      [...jobs(text)]
        .filter(([, body]) => RUNS_THE_TREE.test(body))
        .map(([job]) => `${name}:${job}`),
    );
    expect(running).toEqual(
      expect.arrayContaining(["ci.yml:check", "ci.yml:zone-a", "deploy.yml:gate"]),
    );
    const holding = workflows.flatMap(({ name, text }) =>
      [...jobs(text)]
        .filter(([, body]) => body.includes("id-token: write"))
        .map(([job]) => `${name}:${job}`),
    );
    expect(holding).toEqual(["deploy.yml:deploy", "plan.yml:plan"]);
  });
});

// Every checkout drops its credentials. actions/checkout otherwise leaves the job's GITHUB_TOKEN
// in .git/config, where every install script, test and tool the job runs afterwards can read it;
// no workflow here pushes with git, and a step that calls the API is given the token by name.
// The deploy job's was the last to change (audit B08).

describe("every checkout", () => {
  const checkouts = workflows.flatMap(({ name, text }) =>
    [...jobs(text)].flatMap(([job, body]) =>
      body
        .split(/\n(?= {6}- )/)
        .filter((step) => step.includes("uses: actions/checkout@"))
        .map((step) => ({ where: `${name}:${job}`, step })),
    ),
  );

  it("finds the checkouts it is about", () => {
    expect(checkouts.map(({ where }) => where)).toEqual(
      expect.arrayContaining([
        "ci.yml:check",
        "ci.yml:official-validation",
        "ci.yml:zone-a",
        "ci.yml:agent",
        "deploy.yml:gate",
        "deploy.yml:deploy",
        "plan.yml:plan",
        "vulnerabilities.yml:scan",
      ]),
    );
  });

  it("keeps no credentials in the repository it checks out", () => {
    const persisting = checkouts
      .filter(({ step }) => !/^ {10}persist-credentials: false$/m.test(step))
      .map(({ where }) => where);
    expect(persisting).toEqual([]);
  });
});
