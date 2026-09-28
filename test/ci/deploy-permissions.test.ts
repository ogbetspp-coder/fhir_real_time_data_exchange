import { readFileSync, readdirSync } from "node:fs";

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

  it("deploys only after the gate, installing packages without their scripts", () => {
    const deploy = byName.get("deploy") ?? "";
    expect(deploy).toMatch(/^ {4}needs: gate$/m);
    const install = /- name: Install Node dependencies[^\n]*\n((?: {8}.*\n)+)/.exec(deploy)?.[1];
    expect(install).toBeDefined();
    expect(install).toMatch(/npm_config_ignore_scripts: "true"/);
    // Nothing installs anything else in the job that holds the grant.
    expect(deploy.match(/deploy\.sh deps|npm (ci|install)\b/g)).toHaveLength(1);
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
      expect.arrayContaining([
        "ci.yml:check",
        "ci.yml:zone-a",
        "deploy.yml:gate",
        "deploy.yml:deploy",
      ]),
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
// The deploy job's checkout is the one not yet changed: deploy.yml is changed only in its own
// reviewed pull request. Remove it from this list when that lands.
const NOT_YET_WITHOUT_CREDENTIALS = new Set(["deploy.yml:deploy"]);

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
        "plan.yml:plan",
        "vulnerabilities.yml:scan",
      ]),
    );
  });

  it("keeps no credentials in the repository it checks out", () => {
    const persisting = checkouts
      .filter(({ step }) => !/^ {10}persist-credentials: false$/m.test(step))
      .map(({ where }) => where);
    for (const where of persisting) {
      expect([where, NOT_YET_WITHOUT_CREDENTIALS.has(where)]).toEqual([where, true]);
    }
  });
});
