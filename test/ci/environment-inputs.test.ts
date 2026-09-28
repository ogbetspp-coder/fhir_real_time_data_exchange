import { spawnSync } from "node:child_process";
import { readFileSync, readdirSync } from "node:fs";

import { describe, expect, it } from "vitest";

// The plan is what applies (audit I-6). A pull request's plan (.github/workflows/plan.yml) is the
// last point a person sees an infrastructure change before the deploy applies it unattended. Until
// 2026-09-27 the deploy set QUERY_LOG_REJECTION_REASON and the plan did not, so every plan showed
// an update to the query service that no pull request had made, and reviewers learned to skip a
// "1 to change" line. These pin: the environment's own inputs live in one file deploy.sh reads for
// both, and every other input the deploy hands deploy.sh, the plan hands it too.

const deployWorkflow = readFileSync(".github/workflows/deploy.yml", "utf8");
const planWorkflow = readFileSync(".github/workflows/plan.yml", "utf8");
const deployScript = readFileSync("scripts/gcp/deploy.sh", "utf8");
const environments = readdirSync("scripts/gcp/environments").filter((name) =>
  name.endsWith(".env"),
);
const DEV_PROJECT =
  /^EXPECTED_PROJECT_ID=(\S+)$/m.exec(
    readFileSync("scripts/gcp/environments/dev.env", "utf8"),
  )?.[1] ?? "";

// The keys of the `env:` mapping that starts right after `anchor`, at `indent` spaces.
function envKeys(text: string, anchor: string, indent: number): string[] {
  const from = text.indexOf(anchor);
  if (from === -1) throw new Error(`${anchor} not found`);
  const pad = " ".repeat(indent);
  const header = text.indexOf(`\n${pad}env:\n`, from);
  if (header === -1) throw new Error(`no env: after ${anchor}`);
  const keys: string[] = [];
  const pattern = new RegExp(`^${pad}  ([A-Za-z_][A-Za-z0-9_]*):`);
  for (const line of text.slice(header + indent + 6).split("\n")) {
    if (line.trim() === "" || line.trimStart().startsWith("#")) continue;
    const key = pattern.exec(line)?.[1];
    if (key === undefined) break;
    keys.push(key);
  }
  return keys;
}

const deployInputs = [
  ...envKeys(deployWorkflow, "\n  deploy:\n", 4),
  ...envKeys(deployWorkflow, "- name: Apply infrastructure", 8),
];
const planInputs = [
  ...envKeys(planWorkflow, "\n  plan:\n", 4),
  ...envKeys(planWorkflow, "- name: Plan against live state", 8),
];

function inputsIn(file: string): string[] {
  return readFileSync(`scripts/gcp/environments/${file}`, "utf8")
    .split("\n")
    .filter((line) => line.trim() !== "" && !line.startsWith("#"))
    .map((line) => /^([A-Z][A-Z0-9_]*)=/.exec(line)?.[1] ?? `unreadable line: ${line}`);
}

describe("the plan's inputs", () => {
  it("include every input the deploy passes to deploy.sh", () => {
    expect(deployInputs).toContain("EMA_FLOW_ENVIRONMENT");
    expect(deployInputs).toContain("ALERT_NOTIFICATION_EMAIL");
    // The one exception is the deploy's acknowledgement of a destroy (audit B08, D-2): it names
    // the commit being deployed, which a pull request's plan has not got; the plan's own
    // acknowledgement is the allow-replace label (ALLOW_REPLACE).
    expect(deployInputs).toContain("ALLOW_REPLACE_COMMIT");
    expect(planInputs).toContain("ALLOW_REPLACE");
    const missing = deployInputs.filter(
      (name) => !planInputs.includes(name) && name !== "ALLOW_REPLACE_COMMIT",
    );
    expect(missing).toEqual([]);
  });

  it("name the same environment", () => {
    const environment = (text: string) => /^ {6}EMA_FLOW_ENVIRONMENT: (\S+)$/m.exec(text)?.[1];
    expect(environment(planWorkflow)).toBe(environment(deployWorkflow));
  });
});

describe("each environment's own inputs", () => {
  it("exist for every environment Terraform accepts", () => {
    expect(environments.sort()).toEqual(["dev.env", "prod.env", "validation.env"]);
  });

  it.each(environments)("%s holds NAME=value lines that deploy.sh reads", (file) => {
    for (const name of inputsIn(file)) {
      expect([name, deployScript.includes(`\${${name}:-`)]).toEqual([name, true]);
    }
  });

  it("are set nowhere else", () => {
    const names = environments.flatMap(inputsIn);
    expect(names).toEqual(
      expect.arrayContaining(["QUERY_LOG_REJECTION_REASON", "ALLOW_SYNTHETIC_SOURCES"]),
    );
    for (const name of names) {
      expect([
        name,
        deployWorkflow.includes(`${name}:`),
        planWorkflow.includes(`${name}:`),
      ]).toEqual([name, false, false]);
    }
  });

  it("are read by deploy.sh for the environment it runs, and a missing file is refused", () => {
    const run = (environment: string) =>
      spawnSync("bash", ["scripts/gcp/deploy.sh", "no-such-phase"], {
        encoding: "utf8",
        env: {
          PATH: process.env.PATH ?? "",
          GOOGLE_CLOUD_PROJECT: DEV_PROJECT,
          EMA_FLOW_ENVIRONMENT: environment,
        },
      });
    const dev = run("dev");
    expect(dev.stderr).toContain("Unknown deploy phase: no-such-phase");
    const unknown = run("staging");
    expect(unknown.status).toBe(1);
    expect(unknown.stderr).toContain("No inputs file for environment staging");
    expect(unknown.stderr).not.toContain("Unknown deploy phase");
  });
});

// The environment names its project, and nothing defaults to dev (audit B08, L1). Until then an
// unset or empty EMA_FLOW_ENVIRONMENT read as dev, and dev's inputs (alerts that page no one,
// synthetic sources) applied to whatever project the shell named. Each case runs deploy.sh itself,
// with a phase that does not exist, so it stops at the first refusal or at "Unknown deploy phase".
function deployWith(env: Record<string, string>) {
  return spawnSync("bash", ["scripts/gcp/deploy.sh", "no-such-phase"], {
    encoding: "utf8",
    env: { PATH: process.env.PATH ?? "", ...env },
  });
}

describe("the environment deploy.sh deploys", () => {
  it("is named by dev.env's project, and by no other environment's yet", () => {
    expect(DEV_PROJECT).toMatch(/^[a-z][a-z0-9-]{4,28}[a-z0-9]$/);
    for (const file of environments.filter((name) => name !== "dev.env")) {
      expect([file, inputsIn(file).includes("EXPECTED_PROJECT_ID")]).toEqual([file, false]);
    }
  });

  it.each([
    ["unset", {}],
    ["empty", { EMA_FLOW_ENVIRONMENT: "" }],
  ])("is required: %s is refused, not read as dev", (_label, env) => {
    const run = deployWith({ GOOGLE_CLOUD_PROJECT: DEV_PROJECT, ...env });
    expect(run.status).not.toBe(0);
    expect(run.stderr).toContain("EMA_FLOW_ENVIRONMENT names the environment to deploy");
    expect(run.stderr).not.toContain("Unknown deploy phase");
  });

  it("is deployed only to the project its inputs file names", () => {
    const run = deployWith({
      GOOGLE_CLOUD_PROJECT: "another-project",
      EMA_FLOW_ENVIRONMENT: "dev",
    });
    expect(run.status).toBe(1);
    expect(run.stderr).toContain(`dev is deployed to ${DEV_PROJECT} only`);
    expect(run.stderr).not.toContain("Unknown deploy phase");
  });

  it.each(["prod", "validation"])("is refused as %s, which names no project yet", (environment) => {
    const run = deployWith({
      GOOGLE_CLOUD_PROJECT: DEV_PROJECT,
      EMA_FLOW_ENVIRONMENT: environment,
    });
    expect(run.status).toBe(1);
    expect(run.stderr).toContain(
      `names no EXPECTED_PROJECT_ID, so ${environment} cannot be deployed`,
    );
  });

  it("takes an input its file leaves unset from its default, never from the caller's shell", () => {
    // prod.env sets nothing; a shell that still exports dev's values must not carry them over.
    const unsetLine = /^unset .+$/m.exec(deployScript)?.[0] ?? "";
    const cleared = unsetLine.split(" ").slice(1);
    for (const name of new Set(environments.flatMap(inputsIn))) {
      expect([name, cleared.includes(name)]).toEqual([name, true]);
    }
    expect(deployScript.indexOf(unsetLine)).toBeGreaterThan(-1);
    expect(deployScript.indexOf(unsetLine)).toBeLessThan(
      deployScript.indexOf('source "$ENVIRONMENT_INPUTS"'),
    );
    const run = spawnSync(
      "bash",
      [
        "-c",
        `${unsetLine}; source scripts/gcp/environments/prod.env; printf '%s' "\${ALLOW_SYNTHETIC_SOURCES:-false}"`,
      ],
      {
        encoding: "utf8",
        env: { PATH: process.env.PATH ?? "", ALLOW_SYNTHETIC_SOURCES: "true" },
      },
    );
    expect(run.stdout).toBe("false");
  });
});

// Dev's alerts may page no one (owner decision, 2026-09-28): dev.env relaxes the recipient, and
// the workflows give dev none, so a placeholder left in the repository variable is ignored rather
// than refused. Every other environment keeps the strict rules; these pin that the exception
// cannot reach one.
describe("the alert recipient's dev exception", () => {
  const valueIn = (file: string, name: string) =>
    readFileSync(`scripts/gcp/environments/${file}`, "utf8")
      .split("\n")
      .find((line) => line.startsWith(`${name}=`))
      ?.slice(name.length + 1);

  it("is set by dev.env alone", () => {
    expect(valueIn("dev.env", "REQUIRE_ALERT_RECIPIENT")).toBe("false");
    for (const file of environments.filter((name) => name !== "dev.env")) {
      expect([file, inputsIn(file).includes("REQUIRE_ALERT_RECIPIENT")]).toEqual([file, false]);
    }
  });

  it("withholds the alert variables from dev alone, in the plan as in the deploy", () => {
    for (const name of ["ALERT_NOTIFICATION_EMAIL", "ALERT_NOTIFICATION_CHANNELS"]) {
      const passed = `${name}: \${{ env.EMA_FLOW_ENVIRONMENT != 'dev' && vars.${name} || '' }}`;
      const occurrences = (text: string) =>
        text.split("\n").filter((line) => line.trim().startsWith(`${name}:`));
      // The deploy's configuration check reads the variables as they are, to say they are ignored.
      expect(occurrences(deployWorkflow).map((line) => line.trim())).toEqual([
        `${name}: \${{ vars.${name} }}`,
        passed,
      ]);
      expect(occurrences(planWorkflow).map((line) => line.trim())).toEqual([passed]);
    }
  });

  function stepScript(text: string, name: string): string {
    const start = text.indexOf(`- name: ${name}\n`);
    if (start === -1) throw new Error(`no step ${name}`);
    const run = text.indexOf("        run: |\n", start);
    const lines: string[] = [];
    for (const line of text.slice(run + "        run: |\n".length).split("\n")) {
      if (line !== "" && !line.startsWith("          ")) break;
      lines.push(line.slice(10));
    }
    return lines.join("\n");
  }

  const check = (env: Record<string, string>) =>
    spawnSync("bash", ["-c", stepScript(deployWorkflow, "Check deployment configuration")], {
      encoding: "utf8",
      env: {
        PATH: process.env.PATH ?? "",
        GCP_PROJECT_ID: "p",
        GCP_REGION: "europe-west4",
        GCP_DEPLOY_SERVICE_ACCOUNT: "sa",
        GCP_WORKLOAD_IDENTITY_PROVIDER: "wip",
        ...env,
      },
    });

  it("lets the deploy's configuration check pass dev with no recipient, and says one is ignored", () => {
    const none = check({ EMA_FLOW_ENVIRONMENT: "dev" });
    expect(none.status).toBe(0);
    expect(none.stdout).not.toContain("ignored");
    const placeholder = check({
      EMA_FLOW_ENVIRONMENT: "dev",
      ALERT_NOTIFICATION_EMAIL: "you@khsadvisory.com",
    });
    expect(placeholder.status).toBe(0);
    expect(placeholder.stdout).toContain("Alert variables ignored");
    expect(placeholder.stdout).not.toContain("you@khsadvisory.com");
  });

  it.each(["prod", "validation"])(
    "keeps the deploy's configuration check strict in %s",
    (environment) => {
      const none = check({ EMA_FLOW_ENVIRONMENT: environment });
      expect(none.status).toBe(1);
      expect(none.stderr).toContain("ALERT_NOTIFICATION_EMAIL (or ALERT_NOTIFICATION_CHANNELS)");
      expect(
        check({ EMA_FLOW_ENVIRONMENT: environment, ALERT_NOTIFICATION_CHANNELS: "c" }).status,
      ).toBe(0);
    },
  );
});
