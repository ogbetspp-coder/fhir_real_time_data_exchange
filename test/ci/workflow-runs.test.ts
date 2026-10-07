import { execFile } from "node:child_process";
import { mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { createServer, type Server } from "node:http";
import type { AddressInfo } from "node:net";
import { tmpdir } from "node:os";
import path from "node:path";
import { promisify } from "node:util";

import { afterAll, beforeAll, describe, expect, it } from "vitest";

import {
  API_TRIES,
  ApiError,
  AWAITED_JOBS,
  MUTATING_STEPS,
  rateLimitReset,
  retryDelay,
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

// The part of GitHub's expression language a concurrency block of ours uses: property paths,
// '...' strings, == (case-insensitive for strings), && and || (each yielding an operand, not a
// boolean), and parentheses. Anything else is refused, so a group written in another form fails
// the test rather than being read wrongly.
type Context = { github: Record<string, string> };
type Value = string | boolean | undefined;
function evaluate(expression: string, context: Context): Value {
  const tokens = expression.match(/\s*('[^']*'|[A-Za-z_][\w.]*|==|&&|\|\||\(|\)|\S)/g) ?? [];
  const list = tokens.map((token) => token.trim());
  let at = 0;
  const unread = (): never => {
    throw new Error(`Unread expression: ${expression}`);
  };
  const truthy = (value: Value): boolean => value !== undefined && value !== "" && value !== false;
  const primary = (): Value => {
    const token = list[at++] ?? unread();
    if (token === "(") {
      const value = or();
      if (list[at++] !== ")") unread();
      return value;
    }
    if (token.startsWith("'")) return token.slice(1, -1);
    const path = /^github\.([a-z_]+)$/.exec(token);
    return path === null ? unread() : context.github[path[1] ?? ""];
  };
  const equality = (): Value => {
    const left = primary();
    if (list[at] !== "==") return left;
    at += 1;
    const right = primary();
    return String(left).toLowerCase() === String(right).toLowerCase();
  };
  const and = (): Value => {
    let value = equality();
    while (list[at] === "&&") {
      at += 1;
      const right = equality();
      value = truthy(value) ? right : value;
    }
    return value;
  };
  const or = (): Value => {
    let value = and();
    while (list[at] === "||") {
      at += 1;
      const right = and();
      value = truthy(value) ? value : right;
    }
    return value;
  };
  const value = or();
  if (at !== list.length) unread();
  return value;
}
function interpolate(text: string, context: Context): string {
  return text.replace(/\$\{\{(.*?)\}\}/g, (_, expression: string) =>
    String(evaluate(expression, context)),
  );
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
const succeeded = [...AWAITED_JOBS, "Renderer"].map((name) => ({
  name,
  status: "completed",
  conclusion: "success",
}));
const setJob = (name: string, fields: Partial<WorkflowJob>) =>
  succeeded.map((job) => (job.name === name ? { ...job, ...fields } : job));

describe("the CI the deploy waits for", () => {
  it("names every job of ci.yml but Renderer and Word drawing, by its check's name", () => {
    const names = [...jobs(ci).values()].map((job) => /^ {4}name: (.+)$/m.exec(job)?.[1]);
    expect([...AWAITED_JOBS, "Renderer", "Word drawing"].sort()).toEqual(names.sort());
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

  // A concurrency group holds one running and one pending run, and a newer run cancels the pending
  // one whatever cancel-in-progress says. So the invariant is that no two pushes share a group:
  // the group and cancel-in-progress are evaluated here for the events CI runs on.
  it("puts no two pushes to main in one concurrency group, and cancels only pull requests", () => {
    const block = /^concurrency:\n {2}group: (.+)\n {2}cancel-in-progress: (.+)$/m.exec(ci);
    expect(block).not.toBeNull();
    const [, group = "", cancel = ""] = block ?? [];
    const push = (sha: string): Context => ({
      github: { event_name: "push", ref: "refs/heads/main", sha },
    });
    const pr = (sha: string): Context => ({
      github: { event_name: "pull_request", ref: "refs/pull/7/merge", sha },
    });
    const pushes = ["a", "b", "c"].map((c) => interpolate(group, push(c.repeat(40))));
    expect(new Set(pushes).size).toBe(3);
    // A pull request's runs share one group, its own, and a newer one cancels the older.
    expect(interpolate(group, pr("d".repeat(40)))).toBe(interpolate(group, pr("e".repeat(40))));
    expect(pushes).not.toContain(interpolate(group, pr("a".repeat(40))));
    expect(interpolate(cancel, pr("d".repeat(40)))).toBe("true");
    expect(interpolate(cancel, push("a".repeat(40)))).toBe("false");
  });

  it("evaluates expressions as GitHub does, for the forms the group uses", () => {
    const context: Context = { github: { event_name: "push", ref: "r", sha: "s" } };
    expect(evaluate("github.event_name == 'push' && github.ref || github.sha", context)).toBe("r");
    expect(evaluate("github.event_name == 'x' && github.ref || github.sha", context)).toBe("s");
    expect(evaluate("(github.event_name == 'PUSH')", context)).toBe(true);
    expect(() => evaluate("github.ref != 'r'", context)).toThrow("Unread expression");
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

  it("passes once every job but Renderer succeeded, whether or not the run has finished", () => {
    const running = run({ status: "in_progress", conclusion: null });
    expect(ciVerdict(undefined, []).state).toBe("wait");
    expect(ciVerdict(running, []).state).toBe("wait");
    expect(
      ciVerdict(running, setJob("Images", { status: "in_progress", conclusion: null })),
    ).toEqual({ state: "wait", reason: "CI is in_progress; waiting for Images" });
    // Renderer running or red does not hold the deploy; it is a required pull-request check.
    const rendering = setJob("Renderer", { status: "in_progress", conclusion: null });
    expect(ciVerdict(running, rendering).state).toBe("pass");
    const red = setJob("Renderer", { conclusion: "failure" });
    expect(ciVerdict(run({ conclusion: "failure" }), red).state).toBe("pass");
    // A job it awaits that failed fails at once; one skipped is not one passed.
    expect(ciVerdict(running, setJob("Zone A", { conclusion: "failure" }))).toEqual({
      state: "fail",
      reason: "CI jobs not successful: Zone A",
    });
    expect(ciVerdict(run({}), setJob("Agent", { conclusion: "skipped" })).state).toBe("fail");
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

describe("the API's retries", () => {
  it("asks again after a server error or a rate limit, and not after any other answer", () => {
    expect(retryDelay(502, {}, 0, 100)).toBe(100);
    expect(retryDelay(503, {}, 1, 100)).toBe(200);
    expect(retryDelay(429, { "retry-after": "3" }, 0, 100)).toBe(3_000);
    expect(retryDelay(429, {}, 0, 100)).toBe(100);
    expect(retryDelay(403, { "retry-after": "999" }, 0, 100)).toBe(60_000);
    expect(retryDelay(403, { "x-ratelimit-remaining": "0" }, 0, 100)).toBe(100);
    expect(retryDelay(403, {}, 0, 100)).toBeUndefined();
    expect(retryDelay(401, {}, 0, 100)).toBeUndefined();
    expect(retryDelay(404, {}, 0, 100)).toBeUndefined();
    expect(API_TRIES).toBe(3);
  });
});

// The script itself, against a stand-in for the API that answers from `routes`, after first
// answering each route's queued `failures`, and records every request it was sent.
describe("scripts/ci/workflow-runs.mjs", () => {
  let server: Server;
  let base = "";
  let routes: Record<string, unknown> = {};
  let failures: Record<
    string,
    { status: number; headers?: Record<string, string>; body?: string }[]
  > = {};
  const requests: string[] = [];
  let work = "";

  beforeAll(async () => {
    server = createServer((request, response) => {
      requests.push(request.url ?? "");
      const route = (request.url ?? "").split("?")[0] ?? "";
      const failure = failures[route]?.shift();
      if (failure !== undefined) {
        response.writeHead(failure.status, failure.headers ?? {});
        response.end(failure.body ?? "{}");
        return;
      }
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
          GITHUB_REF: "refs/heads/main",
          AWAIT_CI_MINUTES: "0",
          WORKFLOW_RUNS_BACKOFF_MS: "10",
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

    // Renderer red: not awaited. Official validation red: refused.
    const jobsRoute = "/repos/owner/repo/actions/runs/42/jobs";
    routes[jobsRoute] = { jobs: setJob("Renderer", { conclusion: "failure" }) };
    expect(await node(["await-ci"])).toMatchObject({ code: 0 });
    routes[jobsRoute] = { jobs: setJob("Official validation", { conclusion: "failure" }) };
    const red = await node(["await-ci"]);
    expect(red.code).toBe(1);
    expect(red.stdout).toContain(
      "::error::The deploy waits for CI on this commit, and CI jobs not successful: Official validation.",
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

  it("refuses a run on any ref but main at once, without asking the API", async () => {
    routes = {
      "/repos/owner/repo/actions/workflows/ci.yml/runs": { workflow_runs: [run({ id: 42 })] },
      "/repos/owner/repo/actions/runs/42/jobs": { jobs: succeeded },
    };
    requests.length = 0;
    for (const ref of ["refs/heads/feature", "refs/tags/v1", ""]) {
      const refused = await node(["await-ci"], { GITHUB_REF: ref, AWAIT_CI_MINUTES: "75" });
      expect([ref, refused.code]).toEqual([ref, 1]);
      expect(refused.stdout).toContain("::error::Only refs/heads/main deploys");
    }
    expect(requests).toEqual([]);
  }, 60_000);

  it("asks again after a server error or a rate limit, three times at most", async () => {
    const runs = "/repos/owner/repo/actions/workflows/ci.yml/runs";
    routes = {
      [runs]: { workflow_runs: [run({ id: 42 })] },
      "/repos/owner/repo/actions/runs/42/jobs": { jobs: succeeded },
    };
    failures = { [runs]: [{ status: 502 }, { status: 429, headers: { "retry-after": "0" } }] };
    const recovered = await node(["await-ci"]);
    expect(recovered.code).toBe(0);
    expect(recovered.stdout).toContain("answered 502; asking again");
    expect(recovered.stdout).toContain("answered 429; asking again in 0 ms");

    failures = {};
  }, 60_000);

  // A poll whose retries are spent is "not yet": the wait goes on to the deadline.
  const waiting = { AWAIT_CI_MINUTES: "0.5", AWAIT_CI_POLL_SECONDS: "0.05" };

  it("treats a poll that could not be answered as not yet, and polls again", async () => {
    const runs = "/repos/owner/repo/actions/workflows/ci.yml/runs";
    routes = {
      [runs]: { workflow_runs: [run({ id: 42 })] },
      "/repos/owner/repo/actions/runs/42/jobs": { jobs: succeeded },
    };
    // Three 503s spend one poll's retries; a 200 that is not JSON three times spends the next.
    failures = {
      [runs]: [
        { status: 503 },
        { status: 503 },
        { status: 503 },
        { status: 200, body: "<html>" },
        { status: 200, body: "<html>" },
        { status: 200, body: "<html>" },
      ],
    };
    const recovered = await node(["await-ci"], waiting);
    expect(recovered.code).toBe(0);
    expect(recovered.stdout).toContain(
      `::warning::GitHub API ${runs} answered 503; not yet, polling again.`,
    );
    expect(recovered.stdout).toContain(
      `::warning::GitHub API ${runs} answered 200 with a body that is not JSON; not yet`,
    );

    // Still unanswered at the deadline: refused, naming why.
    failures = { [runs]: [{ status: 503 }, { status: 503 }, { status: 503 }] };
    const down = await node(["await-ci"]);
    expect(down.code).toBe(1);
    expect(down.stdout).toContain(
      `::error::CI on this commit did not finish within 0 minutes: GitHub API ${runs} answered 503.`,
    );
    failures = {};
  }, 60_000);

  it("fails at once on a final answer: 401, a plain 403, 404", async () => {
    const runs = "/repos/owner/repo/actions/workflows/ci.yml/runs";
    routes = { [runs]: { workflow_runs: [run({ id: 42 })] } };
    for (const status of [401, 403, 404]) {
      requests.length = 0;
      failures = { [runs]: [{ status }] };
      const refused = await node(["await-ci"], { AWAIT_CI_MINUTES: "75" });
      expect([status, refused.code]).toEqual([status, 1]);
      expect(refused.stdout).toContain(`::error::GitHub API ${runs} answered ${status}`);
      expect([status, requests]).toEqual([status, [expect.stringContaining(runs)]]);
    }
    failures = {};
  }, 60_000);

  it("waits for a primary rate limit's reset, never past the deadline", async () => {
    const runs = "/repos/owner/repo/actions/workflows/ci.yml/runs";
    routes = {
      [runs]: { workflow_runs: [run({ id: 42 })] },
      "/repos/owner/repo/actions/runs/42/jobs": { jobs: succeeded },
    };
    const limit = (reset: number) => ({
      status: 403,
      headers: { "x-ratelimit-remaining": "0", "x-ratelimit-reset": String(reset) },
    });
    // Reset a second from now: one request, no retries spent against it, then the wait.
    requests.length = 0;
    failures = { [runs]: [limit(Math.ceil(Date.now() / 1000) + 1)] };
    const reset = await node(["await-ci"], waiting);
    expect(reset.code).toBe(0);
    expect(reset.stdout).toMatch(/rate limit exhausted; waiting until \d{4}-\d\d-\d\dT/);
    expect(requests.filter((url) => url.startsWith(runs))).toHaveLength(2);

    // A reset an hour away, with a deadline three seconds away: the wait stops at the deadline.
    // The poll at the deadline is limited again, so the wait ends there, refused.
    const hour = Math.ceil(Date.now() / 1000) + 3600;
    failures = { [runs]: [limit(hour), limit(hour)] };
    const capped = await node(["await-ci"], { AWAIT_CI_MINUTES: "0.05" });
    expect(capped.code).toBe(1);
    expect(capped.stdout).toContain("did not finish within 0.05 minutes");
    expect(capped.stdout).toContain("rate limit exhausted");
    failures = {};
  }, 60_000);

  it("reads a primary rate limit's reset, and nothing else as one", () => {
    const at = { "x-ratelimit-remaining": "0", "x-ratelimit-reset": "1790000000" };
    expect(rateLimitReset(403, at)).toBe(1_790_000_000_000);
    expect(rateLimitReset(429, at)).toBe(1_790_000_000_000);
    expect(rateLimitReset(403, { ...at, "x-ratelimit-remaining": "5" })).toBeUndefined();
    expect(rateLimitReset(403, { "x-ratelimit-remaining": "0" })).toBeUndefined();
    expect(rateLimitReset(503, at)).toBeUndefined();
    expect(new ApiError("x", { transient: true, resetAt: 1 })).toMatchObject({
      transient: true,
      resetAt: 1,
    });
  });

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
    // Every attempt's jobs are read: an earlier attempt of a re-run may have changed live state.
    expect(requests).toContain("/repos/owner/repo/actions/runs/7/jobs?filter=all&per_page=100");

    // Before the plan, a deploy still changing live state: no plan, and a summary that says so,
    // which replaces the pull request's older plan comment.
    const none = path.join(work, "no-plan.md");
    const before = await node(["deploy-activity", "--since", "now"], { PLAN_SUMMARY: none });
    expect(before.code).toBe(1);
    expect(before.stdout).toContain("so no plan is taken");
    expect(readFileSync(none, "utf8")).toMatch(
      /^\*\*Unreliable plan\.\*\* A deploy is changing what the plan reads \(https:\/\/run\/7\), so no plan is taken\./,
    );

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
