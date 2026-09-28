import { mkdirSync, mkdtempSync, readdirSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";

import { describe, expect, it } from "vitest";

import {
  HELD,
  SNAPSHOT_MAX_AGE_DAYS,
  check,
  pins,
  report,
  verdict,
  workflowInputs,
  workflowPin,
  type Pin,
} from "../../scripts/ci/pin-freshness.mjs";

// The pins Dependabot does not move (audit B07, S-2), each read from the file that defines it and
// compared weekly with upstream (.github/workflows/pin-freshness.yml). Offline here: every pin
// must be readable from the repository as it stands, and the verdict must never count a failed
// lookup as current.

describe("pin freshness", () => {
  it("reads every pin from the file that defines it", () => {
    const list = pins();
    expect(list.map(({ name }) => name)).toEqual([
      "HL7 FHIR validator (validator_cli.jar)",
      "chrome-headless-shell",
      "Liberation fonts",
      "Debian snapshot (renderer)",
      "OSV-Scanner",
      "gitleaks",
      "uv",
      "Terraform",
      "Google Cloud SDK (setup-gcloud version)",
      "Cloud Build docker builder",
      "google-cloud-agentplatform (Agent Engine deploy)",
      "google-cloud-aiplatform (Agent Engine deploy)",
      "cloudpickle (Agent Engine deploy)",
    ]);
    for (const pin of list) expect([pin.name, pin.read()]).toEqual([pin.name, expect.any(String)]);
  });

  it("counts a failed lookup and an unreadable pin as unchecked, never as current", async () => {
    const [first, second] = pins();
    if (first === undefined || second === undefined) throw new Error("no pins");
    const unreadable: Pin = {
      ...second,
      read: () => {
        throw new Error("gone");
      },
    };
    const rows = await check([first, unreadable], () => Promise.reject(new Error("HTTP 503")));
    expect(rows.map(({ status }) => status)).toEqual(["unchecked", "unchecked"]);
    expect(report(rows).behind).toBe(2);
  });

  it("reports a pin behind upstream, and one that matches as current", async () => {
    const [first] = pins();
    if (first === undefined) throw new Error("no pins");
    const pinned = first.read();
    expect((await check([first], () => Promise.resolve(pinned)))[0]?.status).toBe("current");
    expect((await check([first], () => Promise.resolve(`${pinned}.1`)))[0]?.status).toBe("behind");
    expect(report(await check([first], () => Promise.resolve(pinned)))).toMatchObject({
      behind: 0,
    });
  });

  it("holds the renderer's Debian snapshot to an age, not to upstream equality", () => {
    const snapshot = pins().find(({ name }) => name.startsWith("Debian snapshot"));
    if (snapshot === undefined) throw new Error("no snapshot pin");
    const day = 24 * 60 * 60 * 1000;
    const at = (days: number) => new Date(Date.UTC(2026, 8, 1) + days * day).toISOString();
    expect(verdict(snapshot, "20260901T000000Z", at(SNAPSHOT_MAX_AGE_DAYS)).status).toBe("current");
    expect(verdict(snapshot, "20260901T000000Z", at(SNAPSHOT_MAX_AGE_DAYS + 1)).status).toBe(
      "behind",
    );
    expect(verdict(snapshot, "yesterday", at(0)).status).toBe("unchecked");
  });

  // Unpinned, setup-gcloud installed the newest Cloud SDK on every deploy and plan.
  it("pins the Cloud SDK, to one version, wherever it is installed", () => {
    const versions = [".github/workflows/deploy.yml", ".github/workflows/plan.yml"].map((file) => {
      const text = readFileSync(file, "utf8");
      const step =
        /uses: google-github-actions\/setup-gcloud@[^\n]*\n(?:\s+#[^\n]*\n)*\s+with:\n\s+version: "(\d+\.\d+\.\d+)"/.exec(
          text,
        );
      return [file, step?.[1]];
    });
    expect(versions.every(([, version]) => version !== undefined)).toBe(true);
    expect(new Set(versions.map(([, version]) => version)).size).toBe(1);
  });

  // A tool installed in several places was read from one of them (audit B07, review round 1,
  // L-3): uv is pinned in four steps across three workflows.
  it("holds every tool the workflows install to one version, wherever it is installed", () => {
    const actions = new Set<string>();
    for (const file of readdirSync(".github/workflows")) {
      const text = readFileSync(path.join(".github/workflows", file), "utf8");
      for (const [, action] of text.matchAll(/uses: ([\w.-]+\/[\w./-]+)@/g))
        actions.add(action ?? "");
    }
    let repeated = 0;
    for (const action of actions) {
      for (const key of [
        "version",
        "node-version",
        "python-version",
        "java-version",
        "terraform_version",
      ]) {
        const found = workflowInputs(action, key);
        if (found.length > 1) repeated += 1;
        expect([action, key, new Set(found.map(({ value }) => value)).size <= 1]).toEqual([
          action,
          key,
          true,
        ]);
      }
    }
    expect(repeated).toBeGreaterThanOrEqual(5);
    expect(workflowInputs("astral-sh/setup-uv", "version")).toHaveLength(4);
  });

  // A setup step with no version input installs whatever is newest, which the equality above
  // would pass for having nothing to compare (audit B07 follow-up, L-3).
  it.each([
    ["astral-sh/setup-uv", "version"],
    ["hashicorp/setup-terraform", "terraform_version"],
    ["google-github-actions/setup-gcloud", "version"],
    ["actions/setup-node", "node-version"],
    ["actions/setup-python", "python-version"],
    ["actions/setup-java", "java-version"],
  ])("finds a version input on every %s step", (action, key) => {
    const steps = readdirSync(".github/workflows").flatMap((file) => [
      ...readFileSync(path.join(".github/workflows", file), "utf8").matchAll(
        new RegExp(`uses: ${action.replace(/[./-]/g, "\\$&")}@`, "g"),
      ),
    ]);
    expect(steps.length).toBeGreaterThan(0);
    expect(workflowInputs(action, key)).toHaveLength(steps.length);
  });

  it("refuses a pin that differs between the places it is installed", () => {
    const directory = mkdtempSync(path.join(tmpdir(), "pin-freshness-"));
    try {
      mkdirSync(directory, { recursive: true });
      const step = (version: string) =>
        `jobs:\n  a:\n    steps:\n      - name: Set up uv\n        uses: astral-sh/setup-uv@${"0".repeat(40)} # v7\n        with:\n          version: "${version}"\n`;
      writeFileSync(path.join(directory, "a.yml"), step("0.12.17"));
      writeFileSync(path.join(directory, "b.yml"), step("0.12.17"));
      expect(workflowPin("astral-sh/setup-uv", "version", directory)).toBe("0.12.17");
      writeFileSync(path.join(directory, "b.yml"), step("0.12.19"));
      expect(() => workflowPin("astral-sh/setup-uv", "version", directory)).toThrow(
        /differs: a\.yml:7=0\.12\.17, b\.yml:7=0\.12\.19/,
      );
      expect(() =>
        workflowPin("hashicorp/setup-terraform", "terraform_version", directory),
      ).toThrow(/not set anywhere/);
    } finally {
      rmSync(directory, { recursive: true, force: true });
    }
  });

  // Held on the legacy builder until docker:29 is rehearsed (audit B07, review round 1, M-1).
  it("reports the held builder as held, with its reason, not as behind; a moved one is judged", () => {
    const builder = pins().find(({ name }) => name === "Cloud Build docker builder");
    if (builder === undefined) throw new Error("no builder pin");
    const held = HELD["Cloud Build docker builder"];
    expect(builder.read()).toBe(held?.value);
    const upstream = `sha256:${"b".repeat(64)}`;
    const row = verdict(builder, builder.read(), upstream);
    expect(row.status).toBe("held");
    expect(row.detail).toContain("B13");
    expect(report([{ pin: builder, pinned: builder.read(), ...row }]).behind).toBe(0);
    expect(verdict(builder, `sha256:${"c".repeat(64)}`, upstream).status).toBe("behind");
  });

  it("is reported weekly and opens an issue from a job that checks nothing out", () => {
    const workflow = readFileSync(".github/workflows/pin-freshness.yml", "utf8");
    expect(workflow).toContain("run: node scripts/ci/pin-freshness.mjs --report");
    expect(workflow).toMatch(/schedule:\n\s+- cron:/);
    const reporter = workflow.slice(workflow.indexOf("\n  report:"));
    expect(reporter).toContain("issues: write");
    expect(reporter).not.toContain("actions/checkout");
    // One open issue at a time: a later week's report is a comment on it.
    expect(reporter).toMatch(/gh issue list --state open[\s\S]*gh issue comment "\$open"/);
    expect(reporter).toContain('gh issue create --title "pins behind upstream" ');
  });
});
