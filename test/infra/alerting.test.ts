import { spawnSync } from "node:child_process";
import { readFileSync, readdirSync } from "node:fs";
import path from "node:path";

import { describe, expect, it } from "vitest";

import { readInfra, terraformBlocks } from "../support/terraform.js";

// Alerts that page someone (audit I-1, I-2, I-9). Until 2026-09-27 the key availability alert —
// the one-hour guard against the FHIR dataset being disabled — existed only when an optional
// variable was set, the pipeline-failure alert notified a channel list no deploy ever filled, and
// the validation-rejection metric matched a log message that had been renamed. These pin: every
// alert policy always exists, always pages the deploy's recipient, and cannot be applied without
// one; and every log line a metric counts is one the code writes.
//
// The one exception, by the owner's decision of 2026-09-28, is dev while it is early development:
// it may have no recipient, and its policies then exist but page no one. These also pin that the
// exception is dev's alone, and that a placeholder is refused in dev as everywhere else.

const terraform = readInfra();
const blocks = terraformBlocks(terraform);
const block = (type: string, name: string) =>
  blocks.find((candidate) => candidate.type === type && candidate.name === name)?.body ?? "";
const deploy = readFileSync("scripts/gcp/deploy.sh", "utf8");
const variables = readFileSync("infra/variables.tf", "utf8");
const variable = (name: string) =>
  new RegExp(`^variable "${name}" \\{\\n([\\s\\S]*?)^\\}`, "m").exec(variables)?.[1] ?? "";

describe("every alert policy", () => {
  const policies = blocks.filter(({ type }) => type === "google_monitoring_alert_policy");

  it("includes the three that matter", () => {
    expect(policies.map(({ name }) => name).sort()).toEqual([
      "key_availability",
      "pipeline_failures",
      "query_entitlement_denials",
    ]);
  });

  it.each(policies.map(({ name, body }) => [name, body]))(
    "%s always exists, pages the deploy's recipients, and refuses an apply with none outside dev",
    (_name, body) => {
      // At the top of the block (terraform fmt indents it by two); a trigger's count is nested.
      expect(body).not.toMatch(/^ {2}(count|for_each)\s*=/m);
      expect(body).toMatch(
        /^\s*notification_channels\s*=\s*local\.alert_notification_channels\s*$/m,
      );
      expect(body).toMatch(
        /precondition \{\s*condition\s*=\s*!var\.require_alert_recipient \|\| length\(local\.alert_notification_channels\) > 0\n/,
      );
    },
  );

  it("pages channels the deploy's inputs reach", () => {
    // The list is the e-mail channel (from ALERT_NOTIFICATION_EMAIL) and the named channels (from
    // ALERT_NOTIFICATION_CHANNELS), and deploy.sh passes both variables to every plan and apply.
    const locals = /alert_notification_channels = concat\(([\s\S]*?)\)/.exec(terraform)?.[1] ?? "";
    expect(locals).toContain("google_monitoring_notification_channel.alert_email[*].id");
    expect(locals).toContain("var.alert_notification_channels");
    expect(block("google_monitoring_notification_channel", "alert_email")).toMatch(
      /email_address = var\.alert_notification_email/,
    );
    expect(deploy).toContain('-var="alert_notification_email=${alert_notification_email}"');
    expect(deploy).toContain(
      '-var="alert_notification_channels=${alert_notification_channels_json}"',
    );
    expect(deploy).toContain('-var="require_alert_recipient=${require_alert_recipient}"');
  });
});

describe("require_alert_recipient", () => {
  const body = variable("require_alert_recipient");

  it("is a boolean that defaults to requiring a recipient", () => {
    expect(body).toMatch(/^\s*type\s*=\s*bool$/m);
    expect(body).toMatch(/^\s*default\s*=\s*true$/m);
    expect(body).toMatch(/^\s*nullable\s*=\s*false$/m);
  });

  it("is refused as false in any environment but dev, by Terraform itself", () => {
    expect(body).toMatch(
      /validation \{\s*condition\s*=\s*var\.require_alert_recipient \|\| var\.environment == "dev"\n/,
    );
  });

  it("does not relax the placeholder refusal, which reads the address alone", () => {
    const placeholder = /validation \{\s*condition\s*=\s*(!can\(regex\([^\n]*)\n/g;
    const conditions = [...variable("alert_notification_email").matchAll(placeholder)].map(
      (match) => match[1] ?? "",
    );
    expect(conditions).toHaveLength(1);
    expect(conditions[0]).not.toContain("require_alert_recipient");
    expect(conditions[0]).not.toContain("environment");
  });
});

// The deploy's inputs are assembled by tf_deploy_vars; this runs it, outside the rest of the
// script, to see what it does with and without a recipient.
function tfDeployVars(env: Record<string, string>): { status: number | null; out: string } {
  // ENVIRONMENT is deploy.sh's own, from EMA_FLOW_ENVIRONMENT; prod when a case names none, so a
  // case proves the strict rules rather than inheriting dev's exception.
  const functions = ["ema_flow_json_array", "tf_deploy_vars"].map((name) => {
    const body = new RegExp(`^${name}\\(\\) \\{\\n[\\s\\S]*?^\\}$`, "m").exec(deploy)?.[0];
    if (body === undefined) throw new Error(`${name} not found in deploy.sh`);
    return body;
  });
  const script = `${functions.join("\n")}
tf_common_vars=()
tf_deploy_vars sa@p.iam.gserviceaccount.com w v q version || exit $?
printf '%s\\n' "\${TF_DEPLOY_VARS[@]}"
`;
  const run = spawnSync("bash", ["-c", script], {
    encoding: "utf8",
    env: { PATH: process.env.PATH ?? "", ENVIRONMENT: "prod", ...env },
  });
  return { status: run.status, out: `${run.stdout}${run.stderr}` };
}

describe("the deploy's alert recipient", () => {
  it.each(["prod", "validation"])(
    "is required in %s: with no e-mail and no channel, the deploy refuses before Terraform",
    (environment) => {
      const { status, out } = tfDeployVars({ ENVIRONMENT: environment });
      expect(status).not.toBe(0);
      expect(out).toContain("No alert recipient");
    },
  );

  it("is optional in dev, whose inputs file says so: the policies are planned with no channel", () => {
    const { status, out } = tfDeployVars({ ENVIRONMENT: "dev", REQUIRE_ALERT_RECIPIENT: "false" });
    expect(status).toBe(0);
    expect(out).toContain("-var=alert_notification_email=\n");
    expect(out).toContain("-var=alert_notification_channels=[]");
    expect(out).toContain("-var=require_alert_recipient=false");
    expect(out).toContain("Alerts page no one");
  });

  it("is still required in dev when its inputs file does not relax it", () => {
    const { status, out } = tfDeployVars({ ENVIRONMENT: "dev" });
    expect(status).not.toBe(0);
    expect(out).toContain("No alert recipient");
  });

  it.each(["prod", "validation"])(
    "cannot be made optional outside dev, wherever the setting comes from: %j",
    (environment) => {
      const { status, out } = tfDeployVars({
        ENVIRONMENT: environment,
        REQUIRE_ALERT_RECIPIENT: "false",
        ALERT_NOTIFICATION_EMAIL: "oncall@ema-flow-alerts.eu",
      });
      expect(status).not.toBe(0);
      expect(out).toContain("accepted only in dev");
    },
  );

  it("is never relaxed by an unnamed environment: deploy.sh refuses to run without one", () => {
    // Until audit B08 (L1) an unset EMA_FLOW_ENVIRONMENT read as dev, whose inputs file relaxes
    // the recipient, on whatever project the shell named. deploy.sh itself, not tf_deploy_vars.
    const run = spawnSync("bash", ["scripts/gcp/deploy.sh", "plan"], {
      encoding: "utf8",
      env: {
        PATH: process.env.PATH ?? "",
        GOOGLE_CLOUD_PROJECT: "any-project",
        REQUIRE_ALERT_RECIPIENT: "false",
      },
    });
    expect(run.status).not.toBe(0);
    expect(run.stderr).toContain("EMA_FLOW_ENVIRONMENT names the environment to deploy");
    expect(`${run.stdout}${run.stderr}`).not.toContain("require_alert_recipient=false");
  });

  it.each(["False", "no", "0", " false"])(
    "accepts only true or false as the setting: %j",
    (value) => {
      const { status, out } = tfDeployVars({ ENVIRONMENT: "dev", REQUIRE_ALERT_RECIPIENT: value });
      expect(status).not.toBe(0);
      expect(out).toContain("REQUIRE_ALERT_RECIPIENT must be true or false");
    },
  );

  it("is passed to Terraform as an e-mail, a channel list, or both", () => {
    const email = tfDeployVars({ ALERT_NOTIFICATION_EMAIL: "oncall@ema-flow-alerts.eu" });
    expect(email.status).toBe(0);
    expect(email.out).toContain("-var=alert_notification_email=oncall@ema-flow-alerts.eu");
    expect(email.out).toContain("-var=alert_notification_channels=[]");
    // The address itself never reaches the log line tf_deploy_vars prints.
    expect(email.out.split("\n").filter((line) => line.includes("alert configuration"))).toEqual([
      "alert configuration: alert_notification_email is set, alert_notification_channels=2 bytes, require_alert_recipient=true",
    ]);
    expect(email.out).toContain("-var=require_alert_recipient=true");
    expect(email.out).not.toContain("Alerts page no one");

    const channel = tfDeployVars({
      ALERT_NOTIFICATION_CHANNELS: "projects/p/notificationChannels/1",
    });
    expect(channel.status).toBe(0);
    expect(channel.out).toContain(
      '-var=alert_notification_channels=["projects/p/notificationChannels/1"]',
    );
  });

  it.each([
    "security@example.com",
    "ops@EXAMPLE.org",
    "alerts@mail.example.net",
    "you@khsadvisory.com",
    "You@company.eu",
    "oncall@operations.test",
    "alerts@team.invalid",
    "ops@mail.example",
    "root@localhost",
    "root@box.LOCALHOST",
    // A trailing dot names the same domain (a fully qualified name), and passed both patterns
    // until audit B08.
    "ops@operations.test.",
    "security@example.com.",
    "root@localhost.",
  ])("is refused when it is a placeholder, in every environment: %s", (address) => {
    for (const env of [
      { ENVIRONMENT: "prod" },
      { ENVIRONMENT: "validation" },
      { ENVIRONMENT: "dev", REQUIRE_ALERT_RECIPIENT: "false" },
    ]) {
      const { status, out } = tfDeployVars({ ...env, ALERT_NOTIFICATION_EMAIL: address });
      expect([env.ENVIRONMENT, status]).not.toEqual([env.ENVIRONMENT, 0]);
      expect(out).toContain("Placeholder alert recipient");
      expect(out).not.toContain(address);
    }
  });

  it("is used in dev when one is given: optional there, not ignored by deploy.sh", () => {
    const { status, out } = tfDeployVars({
      ENVIRONMENT: "dev",
      REQUIRE_ALERT_RECIPIENT: "false",
      ALERT_NOTIFICATION_EMAIL: "oncall@ema-flow-alerts.eu",
    });
    expect(status).toBe(0);
    expect(out).toContain("-var=alert_notification_email=oncall@ema-flow-alerts.eu");
    expect(out).not.toContain("Alerts page no one");
  });

  it.each([
    "young@company.eu",
    "alerts@example.company.eu",
    "ops@notexample.com",
    "ops@testing.eu",
    "qa@test.company.eu",
  ])("is accepted when it only resembles one: %s", (address) => {
    expect(tfDeployVars({ ALERT_NOTIFICATION_EMAIL: address }).status).toBe(0);
  });

  it("is refused as a placeholder by Terraform too, by the same pattern", () => {
    const terraformPattern = /!can\(regex\("\(\?i\)(.+?)", var\.alert_notification_email\)\)/
      .exec(variables)?.[1]
      ?.replaceAll("\\\\", "\\");
    const shellPattern = /grep -Eiq '([^']+)'/.exec(deploy)?.[1];
    expect(terraformPattern).toBeDefined();
    expect(terraformPattern).toBe(shellPattern);
  });
});

describe("the key availability alert", () => {
  it("replaced the conditional one in place, so no deploy recreates it", () => {
    expect(terraform).toMatch(
      /moved \{\n\s+from = google_monitoring_alert_policy\.key_availability\[0\]\n\s+to\s+= google_monitoring_alert_policy\.key_availability\n\}/,
    );
  });

  it("forgets the old e-mail channel instead of deleting it while policies still name it", () => {
    // A delete would run before the policies stop naming the channel, and the Monitoring API
    // refuses to delete a channel in use (the first deploy after #125, reviewed in #138).
    expect(terraform).toMatch(
      /removed \{\n\s+from = google_monitoring_notification_channel\.query_entitlement_denials_email\n\n\s+lifecycle \{\n\s+destroy = false\n\s+\}\n\}/,
    );
    expect(terraform).not.toMatch(
      /from = google_monitoring_notification_channel\.[\w]+\n\s+to\s+=/,
    );
  });
});

// Every src/ file's text, once.
function sourceText(): string {
  const files: string[] = [];
  const walk = (dir: string) => {
    for (const entry of readdirSync(dir, { withFileTypes: true })) {
      const full = path.join(dir, entry.name);
      if (entry.isDirectory()) walk(full);
      else if (entry.name.endsWith(".ts")) files.push(readFileSync(full, "utf8"));
    }
  };
  walk("src");
  return files.join("\n");
}

describe("the log-based metrics", () => {
  const observability = readFileSync("infra/observability.tf", "utf8");
  const src = sourceText();

  it("count only messages the code logs", () => {
    const messages = [...observability.matchAll(/jsonPayload\.message="([^"]+)"/g)].map(
      (match) => match[1] ?? "",
    );
    expect(messages.length).toBeGreaterThan(0);
    for (const message of messages) {
      expect([message, src.includes(`"${message}"`)]).toEqual([message, true]);
    }
  });

  it("count only stages the code logs", () => {
    const stages = [...observability.matchAll(/jsonPayload\.stage=(?:"([^"]+)"|\(([^)]*)\))/g)]
      .flatMap((match) =>
        match[1] !== undefined
          ? [match[1]]
          : [...(match[2] ?? "").matchAll(/"([^"]+)"/g)].map((m) => m[1] ?? ""),
      )
      .filter((stage) => stage !== "");
    expect(stages).toEqual(
      expect.arrayContaining(["document-gate", "source-preflight", "ema-preflight"]),
    );
    for (const stage of stages) {
      expect([stage, src.includes(`stage: "${stage}"`)]).toEqual([stage, true]);
    }
  });

  it("count every fail-closed rejection the pipeline logs", () => {
    const rejections = [
      ...src.matchAll(/log\("warning", "[^"]*rejected", \{\s*runId,\s*stage: "([^"]+)"/g),
    ].map((match) => match[1] ?? "");
    expect(rejections.length).toBeGreaterThanOrEqual(3);
    const filter = block("google_logging_metric", "validation_rejections");
    for (const stage of rejections) {
      expect([stage, filter.includes(`"${stage}"`)]).toEqual([stage, true]);
    }
  });
});
