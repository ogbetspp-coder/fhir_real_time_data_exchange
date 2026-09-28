import { execFile } from "node:child_process";
import { mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { createServer, type Server } from "node:http";
import type { AddressInfo } from "node:net";
import { tmpdir } from "node:os";
import path from "node:path";
import { promisify } from "node:util";

import { afterAll, beforeAll, describe, expect, it } from "vitest";

import {
  CI_JOBS,
  MUTATING_STEPS,
  ciRunFor,
  ciVerdict,
  mutationWindow,
  overlapping,
  type WorkflowJob,
  type WorkflowRun,
} from "../../scripts/ci/workflow-runs.mjs";

// The deploy waits for CI on the commit it deploys, and a pull request's plan refuses to be read
// as reliable when a deploy changed live state while it planned (audit B15: G-1, and #141's
// review, Low 7). scripts/ci/workflow-runs.mjs holds both; these tests hold it to the workflows it
// reads and run it against a stand-in for GitHub's API.

const SCRIPT = path.resolve("scripts/ci/workflow-runs.mjs");
const COMMIT = "a".repeat(40);
const ci = readFileSync(".github/workflows/ci.yml", "utf8");
const deploy = readFileSync(".github/workflows/deploy.yml", "utf8");
const plan = readFileSync(".github/workflows/plan.yml", "utf8");

function jobs(text: string): Map<string, string> {
  const body = text.slice(text.indexOf("\njobs:\n") + "\njobs:\n".length);
  const found = new Map<string, string>();
  const headers = [...body.matchAll(/^ {2}([a-z][a-z0-9-]*):\n/gm)];
  headers.forEach((header, index) => {
    found.set(
      header[1] ?? "",
      body.slice(header.index + header[0].length, headers[index + 1]?.index),
    );
  });
  return found;
}

// A step's name and its text, in order.
function steps(job: string): { name: string; text: string }[] {
  return job
    .split(/^ {6}- /m)
    .slice(1)
    .map((text) => ({ name: /^name: (.+)$/m.exec(text)?.[1]?.trim() ?? "", text }));
}

const run = (fields: Partial<WorkflowRun>): WorkflowRun => ({
  id: 1,
  head_sha: COMMIT,
  head_branch: "main",
  event: "push",
  status: "completed",
  conclusion: "success",
  ...fields,
});
const succeeded = CI_JOBS.map((name) => ({ name, status: "completed", conclusion: "success" }));

describe("the CI the deploy waits for", () => {
  it("names every job of ci.yml, by its check's name", () => {
    const names = [...jobs(ci).values()].map((job) => /^ {4}name: (.+)$/m.exec(job)?.[1]);
    expect([...CI_JOBS].sort()).toEqual(names.sort());
  });

  it("is waited for in the deploy job before any credential is taken", () => {
    const job = jobs(deploy).get("deploy") ?? "";
    expect(job).toMatch(/^ {6}actions: read$/m);
    const names = steps(job).map(({ name }) => name);
    const wait = steps(job).find(({ name }) => name === "Wait for CI on this commit");
    expect(wait?.text).toMatch(/^ {8}run: node scripts\/ci\/workflow-runs\.mjs await-ci\b/m);
    expect(wait?.text).toContain("COMMIT: ${{ github.sha }}");
    expect(names.indexOf("Wait for CI on this commit")).toBeGreaterThan(-1);
    expect(names.indexOf("Wait for CI on this commit")).toBeLessThan(
      names.indexOf("Authenticate to Google Cloud"),
    );
  });

  it("is never cancelled on main by a later push", () => {
    expect(ci).toMatch(
      /^ {2}cancel-in-progress: \$\{\{ github\.event_name == 'pull_request' \}\}$/m,
    );
  });

  it("is CI's run for a push of exactly this commit to main, the latest of them", () => {
    expect(ciRunFor([], COMMIT)).toBeUndefined();
    // A pull request's run (a fork's branch may be called main), another commit's, another
    // branch's: none is taken for this commit.
    expect(
      ciRunFor(
        [
          run({ id: 5, event: "pull_request" }),
          run({ id: 6, head_sha: "b".repeat(40) }),
          run({ id: 7, head_branch: "feature" }),
        ],
        COMMIT,
      ),
    ).toBeUndefined();
    expect(
      ciRunFor([run({ id: 2 }), run({ id: 3 }), run({ id: 9, event: "pull_request" })], COMMIT)?.id,
    ).toBe(3);
  });

  it("passes only when the run and every one of its jobs succeeded", () => {
    expect(ciVerdict(undefined, []).state).toBe("wait");
    expect(ciVerdict(run({ status: "in_progress", conclusion: null }), []).state).toBe("wait");
    expect(ciVerdict(run({ conclusion: "failure" }), succeeded)).toEqual({
      state: "fail",
      reason: "CI concluded failure",
    });
    expect(ciVerdict(run({ conclusion: "cancelled" }), succeeded).state).toBe("fail");
    // A job skipped is not a job passed, whatever the run concluded.
    const skipped = succeeded.map((job) =>
      job.name === "Renderer" ? { ...job, conclusion: "skipped" } : job,
    );
    expect(ciVerdict(run({}), skipped)).toEqual({
      state: "fail",
      reason: "CI jobs not successful: Renderer",
    });
    expect(
      ciVerdict(
        run({}),
        succeeded.filter(({ name }) => name !== "Official validation"),
      ),
    ).toEqual({
      state: "fail",
      reason: "CI jobs missing: Official validation",
    });
    expect(ciVerdict(run({}), succeeded).state).toBe("pass");
  });
});

describe("a deploy changing what a plan reads", () => {
  it("is every step of the deploy job that runs a phase of deploy.sh that changes anything", () => {
    const phases = new Map<string, string>();
    for (const { name, text } of steps(jobs(deploy).get("deploy") ?? "")) {
      const phase = /deploy\.sh ([a-z-]+)/.exec(text)?.[1];
      if (phase !== undefined) phases.set(name, phase);
    }
    // Every phase the job runs is named here as changing live state or not.
    const reads = new Set(["preflight", "init", "smoke", "query-smoke"]);
    const changes = new Set(["apis", "images", "apply", "record-readers", "bootstrap"]);
    for (const [name, phase] of phases) {
      expect([name, reads.has(phase) || changes.has(phase)]).toEqual([name, true]);
    }
    expect([...MUTATING_STEPS]).toEqual(
      [...phases].filter(([, phase]) => changes.has(phase)).map(([name]) => name),
    );
  });

  const at = (minute: number): string => new Date(Date.UTC(2026, 8, 28, 12, minute)).toISOString();
  const minute = (value: number): number => Date.parse(at(value));
  const job = (fields: Partial<WorkflowJob>): WorkflowJob => ({
    name: "Deploy",
    status: "in_progress",
    conclusion: null,
    steps: [
      {
        name: "Wait for CI on this commit",
        status: "completed",
        conclusion: "success",
        started_at: at(0),
      },
      { name: "Apply infrastructure", status: "in_progress", conclusion: null, started_at: at(10) },
    ],
    ...fields,
  });

  it("begins at the first changing step and ends with the job", () => {
    expect(mutationWindow(job({}))).toEqual({ started: minute(10), ended: null });
    expect(
      mutationWindow(job({ status: "completed", conclusion: "success", completed_at: at(20) })),
    ).toEqual({ started: minute(10), ended: minute(20) });
    // Waiting for CI, or refused before any change, changes nothing.
    expect(
      mutationWindow(
        job({ steps: [job({}).steps?.[0] ?? { name: "", status: "", conclusion: null }] }),
      ),
    ).toBeUndefined();
    const skipped = job({
      steps: [
        {
          name: "Apply infrastructure",
          status: "completed",
          conclusion: "skipped",
          started_at: at(10),
        },
      ],
    });
    expect(mutationWindow(skipped)).toBeUndefined();
  });

  it("overlaps a plan read while the deploy was changing live state, and only then", () => {
    const running = job({});
    const finished = job({ status: "completed", conclusion: "success", completed_at: at(20) });
    // Planned from 15 to 17: the running deploy and the one that finished at 20 overlap.
    expect(overlapping([running, finished], minute(15), minute(17))).toHaveLength(2);
    // Planned from 25 to 27: the finished deploy is behind it.
    expect(overlapping([finished], minute(25), minute(27))).toEqual([]);
    // Planned from 5 to 8: the deploy had not begun changing anything.
    expect(overlapping([running], minute(5), minute(8))).toEqual([]);
    expect(overlapping([{ ...running, name: "Quality gate" }], minute(15), minute(17))).toEqual([]);
  });

  it("is checked before the plan and after it", () => {
    const names = steps(jobs(plan).get("plan") ?? "").map(({ name }) => name);
    const before = names.indexOf("Wait for any deploy changing live state");
    const planned = names.indexOf("Plan against live state");
    const after = names.indexOf("Check no deploy changed live state during the plan");
    expect(before).toBeGreaterThan(-1);
    expect(before).toBeLessThan(planned);
    expect(after).toBeGreaterThan(planned);
    expect(after).toBeLessThan(names.indexOf("Post the summary to the pull request"));
    expect(plan).toContain("node scripts/ci/workflow-runs.mjs deploy-activity --since now");
    expect(plan).toContain('node scripts/ci/workflow-runs.mjs deploy-activity --since "$SINCE"');
    expect(jobs(plan).get("plan")).toMatch(/^ {6}actions: read$/m);
    // Not the deploy's concurrency group: a queued plan would cancel a queued deploy.
    expect(plan).not.toContain("deploy-google-cloud");
  });
});

// The script itself, against a stand-in for the API that answers from `routes`.
describe("scripts/ci/workflow-runs.mjs", () => {
  let server: Server;
  let base = "";
  let routes: Record<string, unknown> = {};
  let work = "";

  beforeAll(async () => {
    server = createServer((request, response) => {
      const route = (request.url ?? "").split("?")[0] ?? "";
      const body = routes[route];
      const authorised = request.headers.authorization === "Bearer test-token";
      response.writeHead(body === undefined || !authorised ? 404 : 200, {
        "content-type": "application/json",
      });
      response.end(JSON.stringify(body ?? {}));
    });
    await new Promise<void>((resolve) => server.listen(0, "127.0.0.1", resolve));
    base = `http://127.0.0.1:${(server.address() as AddressInfo).port}`;
    work = mkdtempSync(path.join(tmpdir(), "workflow-runs-"));
  });

  afterAll(async () => {
    await new Promise((resolve) => server.close(resolve));
    rmSync(work, { recursive: true, force: true });
  });

  async function node(args: string[], env: Record<string, string> = {}) {
    try {
      const { stdout } = await promisify(execFile)(process.execPath, [SCRIPT, ...args], {
        env: {
          PATH: process.env.PATH ?? "",
          GH_TOKEN: "test-token",
          GITHUB_REPOSITORY: "owner/repo",
          GITHUB_API_URL: base,
          COMMIT,
          AWAIT_CI_MINUTES: "0",
          ...env,
        },
        timeout: 30_000,
      });
      return { code: 0, stdout };
    } catch (error) {
      const failed = error as { code?: number; stdout?: string };
      return { code: failed.code ?? -1, stdout: failed.stdout ?? "" };
    }
  }

  it("lets the deploy go on only when CI on its commit succeeded", async () => {
    routes = {
      "/repos/owner/repo/actions/workflows/ci.yml/runs": { workflow_runs: [run({ id: 42 })] },
      "/repos/owner/repo/actions/runs/42/jobs": { jobs: succeeded },
    };
    expect(await node(["await-ci"])).toMatchObject({ code: 0 });

    routes["/repos/owner/repo/actions/runs/42/jobs"] = {
      jobs: succeeded.map((job) =>
        job.name === "Renderer" ? { ...job, conclusion: "failure" } : job,
      ),
    };
    const red = await node(["await-ci"]);
    expect(red.code).toBe(1);
    expect(red.stdout).toContain(
      "::error::The deploy waits for CI on this commit, and CI jobs not successful: Renderer.",
    );

    // No run for this commit (a dispatch on a commit CI never ran for a push): refused at the
    // deadline, never passed.
    routes["/repos/owner/repo/actions/workflows/ci.yml/runs"] = { workflow_runs: [] };
    const none = await node(["await-ci"]);
    expect(none.code).toBe(1);
    expect(none.stdout).toContain(
      "did not finish within 0 minutes: CI has no run for this commit yet",
    );

    // An API that refuses the request fails closed.
    expect((await node(["await-ci"], { GH_TOKEN: "wrong" })).code).toBe(1);
  }, 60_000);

  it("marks a plan that overlapped a deploy unreliable, at the top of its summary", async () => {
    const now = new Date();
    const earlier = new Date(now.getTime() - 10 * 60_000).toISOString();
    routes = {
      "/repos/owner/repo/actions/workflows/deploy.yml/runs": {
        workflow_runs: [
          {
            id: 7,
            status: "in_progress",
            updated_at: now.toISOString(),
            html_url: "https://run/7",
          },
        ],
      },
      "/repos/owner/repo/actions/runs/7/jobs": {
        jobs: [
          {
            name: "Deploy",
            status: "in_progress",
            conclusion: null,
            steps: [
              {
                name: "Apply infrastructure",
                status: "in_progress",
                conclusion: null,
                started_at: earlier,
              },
            ],
          },
        ],
      },
    };
    const summary = path.join(work, "plan-summary.md");
    writeFileSync(summary, "| address | action |\n");
    const after = await node(["deploy-activity", "--since", earlier], { PLAN_SUMMARY: summary });
    expect(after.code).toBe(1);
    expect(readFileSync(summary, "utf8")).toMatch(
      /^\*\*Unreliable plan\.\*\* A deploy changed what the plan reads while it was read \(https:\/\/run\/7\)[^\n]*\n\n\| address \| action \|\n$/,
    );
    const before = await node(["deploy-activity", "--since", "now"]);
    expect(before.code).toBe(1);
    expect(before.stdout).toContain("so no plan is taken");

    // Once the deploy is only waiting for CI, the plan may start, and says from when.
    routes["/repos/owner/repo/actions/runs/7/jobs"] = {
      jobs: [
        {
          name: "Deploy",
          status: "in_progress",
          conclusion: null,
          steps: [
            {
              name: "Wait for CI on this commit",
              status: "in_progress",
              conclusion: null,
              started_at: earlier,
            },
          ],
        },
      ],
    };
    const output = path.join(work, "output");
    writeFileSync(output, "");
    expect(
      (await node(["deploy-activity", "--since", "now"], { GITHUB_OUTPUT: output })).code,
    ).toBe(0);
    expect(readFileSync(output, "utf8")).toMatch(/^since=\d{4}-\d\d-\d\dT[\d:.]+Z\n$/);
  }, 60_000);
});
