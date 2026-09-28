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

const terraform = readInfra();
const blocks = terraformBlocks(terraform);
const block = (type: string, name: string) =>
  blocks.find((candidate) => candidate.type === type && candidate.name === name)?.body ?? "";
const deploy = readFileSync("scripts/gcp/deploy.sh", "utf8");

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
    "%s always exists, pages the deploy's recipients, and refuses an apply with none",
    (_name, body) => {
      // At the top of the block (terraform fmt indents it by two); a trigger's count is nested.
      expect(body).not.toMatch(/^ {2}(count|for_each)\s*=/m);
      expect(body).toMatch(
        /^\s*notification_channels\s*=\s*local\.alert_notification_channels\s*$/m,
      );
      expect(body).toMatch(
        /precondition \{\s*condition\s*=\s*length\(local\.alert_notification_channels\) > 0/,
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
  });
});

// The deploy's inputs are assembled by tf_deploy_vars; this runs it, outside the rest of the
// script, to see what it does with and without a recipient.
function tfDeployVars(env: Record<string, string>): { status: number | null; out: string } {
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
    env: { PATH: process.env.PATH ?? "", ...env },
  });
  return { status: run.status, out: `${run.stdout}${run.stderr}` };
}

describe("the deploy's alert recipient", () => {
  it("is required: with no e-mail and no channel, the deploy refuses before Terraform", () => {
    const { status, out } = tfDeployVars({});
    expect(status).not.toBe(0);
    expect(out).toContain("No alert recipient");
  });

  it("is passed to Terraform as an e-mail, a channel list, or both", () => {
    const email = tfDeployVars({ ALERT_NOTIFICATION_EMAIL: "alerts@example.com" });
    expect(email.status).toBe(0);
    expect(email.out).toContain("-var=alert_notification_email=alerts@example.com");
    expect(email.out).toContain("-var=alert_notification_channels=[]");
    // The address itself never reaches the log line tf_deploy_vars prints.
    expect(email.out.split("\n").filter((line) => line.includes("alert configuration"))).toEqual([
      "alert configuration: alert_notification_email is set, alert_notification_channels=2 bytes",
    ]);

    const channel = tfDeployVars({
      ALERT_NOTIFICATION_CHANNELS: "projects/p/notificationChannels/1",
    });
    expect(channel.status).toBe(0);
    expect(channel.out).toContain(
      '-var=alert_notification_channels=["projects/p/notificationChannels/1"]',
    );
  });
});

describe("the key availability alert", () => {
  it("replaced the conditional one in place, so no deploy recreates it", () => {
    expect(terraform).toMatch(
      /moved \{\n\s+from = google_monitoring_alert_policy\.key_availability\[0\]\n\s+to\s+= google_monitoring_alert_policy\.key_availability\n\}/,
    );
    expect(terraform).toMatch(
      /moved \{\n\s+from = google_monitoring_notification_channel\.query_entitlement_denials_email\n\s+to\s+= google_monitoring_notification_channel\.alert_email\n\}/,
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
