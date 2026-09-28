import { existsSync, readFileSync, readdirSync } from "node:fs";
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

// What the job holding the grant runs (audit B08, D-1). The deploy job's steps call deploy.sh
// phases, and those call functions and scripts; every one of them runs with the deployer's
// credentials file and its OIDC token request in the environment. Until then bootstrap.sh ran
// `npm run standards:fetch` and `node_modules/.bin/tsx`, which load tsx, esbuild and zod at import
// time. So nothing reachable from a phase the deploy job runs may start an installed package, and
// every Node script it starts must import Node's built-ins and the repository's own files only.
const RUNS_A_PACKAGE = /\btsx\b|node_modules\/\.bin|\bnpm (run|ci|install|i|exec|test)\b|\bnpx\b/;

// A script's shell, without the Python its heredocs feed to python3: that text is never run by
// the shell, and a `}` alone on a line inside it would end a function early for the reader below.
function shell(text: string): string {
  return text.replace(/<<'(\w+)'[^\n]*\n[\s\S]*?^\1$/gm, "<<'$1'");
}
const deployScript = shell(readFileSync("scripts/gcp/deploy.sh", "utf8"));

function reachableFromDeployJob(): Map<string, string> {
  const functions = new Map<string, string>();
  for (const match of deployScript.matchAll(/^([a-z_]+)\(\) \{\n[\s\S]*?^\}$/gm)) {
    functions.set(match[1] ?? "", match[0]);
  }
  // Top-level code, which every phase runs: the script with each function body taken out.
  let top = deployScript;
  for (const body of functions.values()) top = top.replace(body, "");
  const dispatch = new Map(
    [...deployScript.matchAll(/^ {2}([a-z-]+)\) (.+) ;;$/gm)].map((m) => [m[1] ?? "", m[2] ?? ""]),
  );
  const phases = [
    ...(byName.get("deploy") ?? "").matchAll(/scripts\/gcp\/deploy\.sh ([a-z-]+)/g),
  ].map((m) => m[1] ?? "");
  expect(phases).toEqual(expect.arrayContaining(["apply", "bootstrap", "record-readers"]));
  const reached = new Map<string, string>([["deploy.sh (top level)", top]]);
  const queue = phases.map((phase) => {
    const call = dispatch.get(phase);
    expect([phase, call]).toEqual([phase, expect.any(String)]);
    return call ?? "";
  });
  while (queue.length > 0) {
    const text = queue.shift() ?? "";
    for (const [name, body] of functions) {
      if (!reached.has(name) && new RegExp(`\\b${name}\\b`).test(text)) {
        reached.set(name, body);
        queue.push(body);
      }
    }
    for (const match of text.matchAll(/\bbash (scripts\/gcp\/[\w.-]+\.sh)/g)) {
      const file = match[1] ?? "";
      if (!reached.has(file)) {
        const body = shell(readFileSync(file, "utf8"));
        reached.set(file, body);
        queue.push(body);
      }
    }
    if (/source .*common\.sh/.test(text) && !reached.has("scripts/gcp/common.sh")) {
      reached.set("scripts/gcp/common.sh", shell(readFileSync("scripts/gcp/common.sh", "utf8")));
    }
  }
  return reached;
}

// The modules a Node script imports, followed through the repository's own files.
function nodeImports(file: string, seen = new Set<string>()): string[] {
  if (seen.has(file)) return [];
  seen.add(file);
  const text = readFileSync(file, "utf8");
  const specifiers = [
    ...text.matchAll(/^import [^;]*?from "([^"]+)";$/gm),
    ...text.matchAll(/\brequire\("([^"]+)"\)/g),
    ...text.matchAll(/\bimport\("([^"]+)"\)/g),
  ].map((match) => match[1] ?? "");
  return specifiers.flatMap((specifier) => {
    if (!specifier.startsWith(".")) return [`${file}: ${specifier}`];
    // TypeScript sources import each other by their compiled .js names.
    const target = path.join(path.dirname(file), specifier);
    return nodeImports(existsSync(target) ? target : target.replace(/\.js$/, ".ts"), seen);
  });
}

describe("the job that can become the deployer", () => {
  const reached = reachableFromDeployJob();

  it("reaches the scripts it is about", () => {
    expect([...reached.keys()]).toEqual(
      expect.arrayContaining([
        "phase_apply",
        "phase_bootstrap",
        "plan_reviewed",
        "scripts/gcp/bootstrap.sh",
        "scripts/gcp/reconcile-fhir-stores.sh",
        "scripts/gcp/record-readers.sh",
      ]),
    );
    // Made by the gate job, and by `deploy.sh all` locally; never by a phase of this job.
    expect(reached.has("phase_inputs")).toBe(false);
    expect(reached.has("phase_deps")).toBe(false);
  });

  it("starts no installed package: no tsx, nothing from node_modules/.bin, no npm run", () => {
    for (const [where, text] of reached) {
      expect([where, RUNS_A_PACKAGE.exec(text)?.[0]]).toEqual([where, undefined]);
    }
  });

  it("starts Node scripts that import Node's built-ins and the repository's own files only", () => {
    const scripts = new Set(
      [...reached.values()].flatMap((text) =>
        [...text.matchAll(/\bnode (scripts\/[\w/.-]+\.m?js)\b/g)].map((m) => m[1] ?? ""),
      ),
    );
    expect([...scripts]).toEqual(
      expect.arrayContaining([
        "scripts/fhir/deploy-inputs.mjs",
        "scripts/fhir/fetch-standards.mjs",
        "scripts/fhir/select-import-resources.mjs",
      ]),
    );
    for (const script of scripts) {
      for (const imported of nodeImports(script)) {
        expect(imported).toMatch(/: node:[a-z/_]+$/);
      }
    }
  });

  it("would have caught the fetch and the fixture export this batch moved to the gate job", () => {
    expect(RUNS_A_PACKAGE.test("npm run standards:fetch")).toBe(true);
    expect(
      RUNS_A_PACKAGE.test("node_modules/.bin/tsx scripts/fhir/export-fixture.ts out.json in.json"),
    ).toBe(true);
    expect(nodeImports("scripts/fhir/export-fixture.ts").some((i) => !i.includes(": node:"))).toBe(
      true,
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
