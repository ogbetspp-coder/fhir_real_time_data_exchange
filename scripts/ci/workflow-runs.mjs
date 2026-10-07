import { appendFileSync, existsSync, readFileSync, writeFileSync } from "node:fs";
import { setTimeout as sleep } from "node:timers/promises";
import { fileURLToPath } from "node:url";

// What the workflows ask GitHub about each other's runs (audit B15). Plain JavaScript with Node's
// built-ins only, so the deploy job, which can become the deployer, runs it without installing
// anything (test/ci/deploy-permissions.test.ts).
//
// await-ci: the deploy waits for CI's run on the commit it deploys, and goes on only when every
// job of it but Renderer and Word drawing succeeded. Until then the deploy's own gate ran `npm run
// check` alone, so a push by an administrator, or a merge while Official validation was red,
// deployed unattended. Renderer is not waited for: the renderer gate is CI-only, nothing the deploy
// ships reads its verdict, and it stays a required check on every pull request. Nor is Word
// drawing: its image is built in CI only, and the deploy ships nothing that reads a drawing
// (docs/design/certified-word-drawing.md, until its images build and pins).
// The run must be CI's run for a push to main of exactly this commit: a run for a pull request, a
// branch or another commit is never taken for it, and a dispatch of the deploy on a commit with no
// such run is refused.
//
// deploy-activity: the plan of a pull request (.github/workflows/plan.yml) reads live state, and a
// deploy changing that state while the plan reads it makes a plan that proposes reverting the
// deploy (#141's plan proposed the query and worker images of 75e3128). The plan does not share the
// deploy's concurrency group, where a queued plan would cancel a queued deploy; instead it asks,
// before planning and after, whether any deploy was changing anything in the meantime, and a plan
// that overlapped one is marked unreliable and fails.
//
// usage:
//   GH_TOKEN=... GITHUB_REPOSITORY=owner/repo COMMIT=<sha> node scripts/ci/workflow-runs.mjs await-ci
//   GH_TOKEN=... GITHUB_REPOSITORY=owner/repo node scripts/ci/workflow-runs.mjs deploy-activity \
//     --since now|<ISO time> [--wait-minutes N]

// CI's jobs the deploy waits for, by the names the checks carry; every one must have succeeded
// (test/ci/workflow-runs.test.ts holds this list, Renderer and Word drawing to
// .github/workflows/ci.yml).
export const AWAITED_JOBS = ["Check", "Official validation", "Images", "Zone A", "Agent"];

// The deploy job's steps that change what a plan reads: from the first to run until the job ends,
// a plan against live state is unreliable (test/ci/workflow-runs.test.ts holds this list to every
// step of the job that runs a phase of deploy.sh, each named as changing or not).
export const DEPLOY_JOB = "Deploy";
export const MUTATING_STEPS = [
  "Enable APIs and Artifact Registry",
  "Build container images",
  "Apply infrastructure",
  "Record readers",
  "Reconcile FHIR stores",
];

// CI's run for a push of `commit` to main, the latest if there are several; undefined if none.
export function ciRunFor(runs, commit) {
  return runs
    .filter((run) => run.head_sha === commit && run.event === "push" && run.head_branch === "main")
    .sort((a, b) => b.id - a.id)[0];
}

// "pass" once each of AWAITED_JOBS has succeeded, whether or not the run has finished; "fail" as
// soon as one has finished otherwise, or when the run finished without one; "wait" until then.
export function ciVerdict(run, jobs) {
  if (run === undefined) return { state: "wait", reason: "CI has no run for this commit yet" };
  const job = (name) => jobs.find((found) => found.name === name);
  const failed = AWAITED_JOBS.filter(
    (name) => job(name)?.status === "completed" && job(name)?.conclusion !== "success",
  );
  if (failed.length > 0)
    return { state: "fail", reason: `CI jobs not successful: ${failed.join(", ")}` };
  const pending = AWAITED_JOBS.filter((name) => job(name)?.conclusion !== "success");
  if (pending.length === 0)
    return { state: "pass", reason: `CI succeeded: ${AWAITED_JOBS.join(", ")}` };
  if (run.status === "completed")
    return { state: "fail", reason: `CI jobs missing: ${pending.join(", ")}` };
  return { state: "wait", reason: `CI is ${run.status}; waiting for ${pending.join(", ")}` };
}

// When a deploy job began changing what a plan reads (its first mutating step's start, skipped
// steps aside), and when it stopped (the job's completion, or null while it runs); undefined for a
// job that changed nothing.
export function mutationWindow(job) {
  const started = (job.steps ?? [])
    .filter(
      (step) =>
        MUTATING_STEPS.includes(step.name) &&
        step.conclusion !== "skipped" &&
        typeof step.started_at === "string",
    )
    .map((step) => Date.parse(step.started_at))
    .sort((a, b) => a - b)[0];
  if (started === undefined) return undefined;
  const ended =
    job.status === "completed" && job.completed_at ? Date.parse(job.completed_at) : null;
  return { started, ended };
}

// The deploy jobs whose window overlaps [since, now]: a plan read in that interval is unreliable.
export function overlapping(jobs, since, now) {
  return jobs.filter((job) => {
    if (job.name !== DEPLOY_JOB) return false;
    const window = mutationWindow(job);
    return (
      window !== undefined &&
      window.started <= now &&
      (window.ended === null || window.ended >= since)
    );
  });
}

// Whether an answer is worth asking again, and after how long: a server error (5xx), or a rate
// limit (429, or 403 with a retry-after or an exhausted quota, GitHub's secondary limits), after
// its retry-after if it gives one (capped at a minute), else after `backoff` doubling per try.
// Anything else (a 401, a 404, a plain 403) is an answer, and fails at once.
export function retryDelay(status, headers, attempt, backoff) {
  const limited =
    status === 429 ||
    (status === 403 &&
      (headers["retry-after"] !== undefined || headers["x-ratelimit-remaining"] === "0"));
  if (status < 500 && !limited) return undefined;
  const after = Number(headers["retry-after"]);
  return Number.isFinite(after) && after >= 0
    ? Math.min(after * 1000, 60_000)
    : backoff * 2 ** attempt;
}

// When a primary rate limit resets (403 or 429 with no requests remaining and a reset time, in
// epoch seconds), in epoch milliseconds; undefined for any other answer. Asking again after a few
// seconds would only spend the retries: the quota comes back at the reset, not before.
export function rateLimitReset(status, headers) {
  if ((status !== 403 && status !== 429) || headers["x-ratelimit-remaining"] !== "0") {
    return undefined;
  }
  const reset = Number(headers["x-ratelimit-reset"]);
  return Number.isFinite(reset) && reset > 0 ? reset * 1000 : undefined;
}

// A failed request. `transient` when asking later may succeed: the retries spent on a server
// error, a connection that failed or timed out, or an answer that was not JSON; or a primary rate
// limit, which also carries when it resets (`resetAt`). Otherwise (a 401, a 404, a plain 403) the
// answer is final.
export class ApiError extends Error {
  constructor(message, { transient, resetAt, cause } = {}) {
    super(message, cause === undefined ? undefined : { cause });
    this.transient = transient === true;
    this.resetAt = resetAt;
  }
}

export const API_TRIES = 3;

async function api(path) {
  const base = process.env.GITHUB_API_URL || "https://api.github.com";
  const token = process.env.GH_TOKEN;
  if (!token) throw new Error("GH_TOKEN is required");
  const backoff = Number(process.env.WORKFLOW_RUNS_BACKOFF_MS ?? "2000");
  const route = path.split("?")[0];
  for (let attempt = 0; ; attempt += 1) {
    const last = attempt + 1 >= API_TRIES;
    let response;
    try {
      response = await fetch(`${base}${path}`, {
        headers: {
          accept: "application/vnd.github+json",
          authorization: `Bearer ${token}`,
          "x-github-api-version": "2022-11-28",
        },
        signal: globalThis.AbortSignal.timeout(30_000),
      });
    } catch (error) {
      // A connection that failed or timed out is retried like a server error.
      if (last) {
        throw new ApiError(`GitHub API ${route} unreachable: ${error}`, {
          transient: true,
          cause: error,
        });
      }
      await sleep(backoff * 2 ** attempt);
      continue;
    }
    if (response.ok) {
      const text = await response.text();
      try {
        return JSON.parse(text);
      } catch (error) {
        // A 200 that is not JSON (a proxy's page, a cut body) is retried like a server error.
        if (last) {
          throw new ApiError(`GitHub API ${route} answered 200 with a body that is not JSON`, {
            transient: true,
            cause: error,
          });
        }
        await sleep(backoff * 2 ** attempt);
        continue;
      }
    }
    const headers = Object.fromEntries(response.headers);
    const resetAt = rateLimitReset(response.status, headers);
    if (resetAt !== undefined) {
      throw new ApiError(`GitHub API ${route} answered ${response.status}: rate limit exhausted`, {
        transient: true,
        resetAt,
      });
    }
    const delay = retryDelay(response.status, headers, attempt, backoff);
    if (delay === undefined || last) {
      throw new ApiError(`GitHub API ${route} answered ${response.status}`, {
        transient: delay !== undefined,
      });
    }
    console.log(`GitHub API ${route} answered ${response.status}; asking again in ${delay} ms.`);
    await sleep(delay);
  }
}

function repository() {
  const repo = process.env.GITHUB_REPOSITORY ?? "";
  if (!/^[\w.-]+\/[\w.-]+$/.test(repo)) throw new Error("GITHUB_REPOSITORY is required");
  return repo;
}

// A run's jobs: of its latest attempt only (`latest`, what CI's verdict reads), or of every
// attempt (`all`): an earlier attempt of a re-run deploy may have changed live state too.
async function jobsOf(repo, runId, filter = "latest") {
  const { jobs } = await api(
    `/repos/${repo}/actions/runs/${runId}/jobs?filter=${filter}&per_page=100`,
  );
  return jobs;
}

async function awaitCi() {
  const repo = repository();
  // Only main deploys. A dispatch on another branch or a tag is refused here, at once, rather than
  // after waiting out the deadline for a push run that will never exist while it holds the deploy's
  // concurrency group.
  if (process.env.GITHUB_REF !== "refs/heads/main") {
    console.log(
      `::error::Only refs/heads/main deploys; this run is on ${process.env.GITHUB_REF || "no ref"}.`,
    );
    return 1;
  }
  const commit = process.env.COMMIT ?? "";
  if (!/^[0-9a-f]{40}$/.test(commit)) throw new Error("COMMIT must be a full commit SHA");
  const minutes = Number(process.env.AWAIT_CI_MINUTES ?? "45");
  const poll = Number(process.env.AWAIT_CI_POLL_SECONDS ?? "30") * 1000;
  const deadline = Date.now() + minutes * 60_000;
  for (;;) {
    // A poll that could not be answered is "not yet", not a verdict: the loop goes on until the
    // deadline (review round 2 of audit B15; one failed poll used to fail the deploy). Only an
    // answer that is final (a 401, a 404, a plain 403) fails at once.
    let verdict;
    let run;
    let wait = poll;
    let limited = false;
    try {
      const { workflow_runs: runs } = await api(
        `/repos/${repo}/actions/workflows/ci.yml/runs?head_sha=${commit}&event=push&branch=main&per_page=20`,
      );
      run = ciRunFor(runs, commit);
      verdict = ciVerdict(run, run ? await jobsOf(repo, run.id) : []);
    } catch (error) {
      if (!(error instanceof ApiError) || !error.transient) throw error;
      run = undefined;
      verdict = { state: "wait", reason: error.message };
      // A primary rate limit: wait for its reset, not a poll, and never past the deadline.
      if (error.resetAt !== undefined) {
        limited = true;
        wait = Math.max(0, Math.min(error.resetAt - Date.now(), deadline - Date.now()));
        console.log(
          `::warning::${error.message}; waiting until ${new Date(Date.now() + wait).toISOString()}.`,
        );
      } else {
        console.log(`::warning::${error.message}; not yet, polling again.`);
      }
    }
    console.log(`CI on ${commit}: ${verdict.reason}${run ? ` (${run.html_url})` : ""}`);
    if (verdict.state === "pass") return 0;
    if (verdict.state === "fail") {
      console.log(`::error::The deploy waits for CI on this commit, and ${verdict.reason}.`);
      return 1;
    }
    if (Date.now() >= deadline || (!limited && Date.now() + poll > deadline)) {
      console.log(
        `::error::CI on this commit did not finish within ${minutes} minutes: ${verdict.reason}.`,
      );
      return 1;
    }
    await sleep(wait);
  }
}

// The Deploy jobs, of every attempt, of the deploy workflow's recent runs that may overlap
// [since, now]: every run not yet completed, and every run updated since `since`.
async function recentDeployJobs(repo, since) {
  const { workflow_runs: runs } = await api(
    `/repos/${repo}/actions/workflows/deploy.yml/runs?per_page=30`,
  );
  const candidates = runs.filter(
    (run) => run.status !== "completed" || Date.parse(run.updated_at) >= since,
  );
  const jobs = [];
  for (const run of candidates) {
    for (const job of await jobsOf(repo, run.id, "all"))
      jobs.push({ ...job, run_url: run.html_url });
  }
  return jobs;
}

// Timestamps are the runner's clock against GitHub's; a minute's margin makes a skew between them
// read as an overlap rather than miss one.
export const CLOCK_MARGIN_MS = 60_000;

// Before planning (--since now): waits up to --wait-minutes for any deploy changing live state to
// finish, then writes `since=<ISO time>` to $GITHUB_OUTPUT, the moment the plan may start from.
// After planning (--since <that time>): fails if any deploy changed live state since then, and
// puts the warning at the top of the plan's summary ($PLAN_SUMMARY), which the pull request shows.
async function deployActivity(args) {
  const repo = repository();
  const at = args.indexOf("--since");
  const given = args[at + 1] ?? "";
  if (at === -1 || (given !== "now" && Number.isNaN(Date.parse(given)))) {
    throw new Error("--since now|<ISO time> is required");
  }
  const sinceNow = () => (given === "now" ? Date.now() - CLOCK_MARGIN_MS : Date.parse(given));
  const waitAt = args.indexOf("--wait-minutes");
  const deadline = Date.now() + (waitAt === -1 ? 0 : Number(args[waitAt + 1])) * 60_000;
  for (;;) {
    const since = sinceNow();
    const found = overlapping(await recentDeployJobs(repo, since), since, Date.now());
    if (found.length === 0) {
      console.log("No deploy is changing, or changed, what the plan reads.");
      if (given === "now" && process.env.GITHUB_OUTPUT) {
        appendFileSync(process.env.GITHUB_OUTPUT, `since=${new Date(since).toISOString()}\n`);
      }
      return 0;
    }
    const urls = found.map((job) => job.run_url).join(", ");
    if (Date.now() + 30_000 > deadline) {
      const message =
        given === "now"
          ? `A deploy is changing what the plan reads (${urls}), so no plan is taken. Re-run this check once the deploy has finished.`
          : `A deploy changed what the plan reads while it was read (${urls}), so this plan may propose reverting that deploy. Re-run this check once the deploy has finished.`;
      console.log(`::error::${message}`);
      const summary = process.env.PLAN_SUMMARY;
      if (summary) {
        const plan = existsSync(summary) ? readFileSync(summary, "utf8") : "";
        writeFileSync(summary, `**Unreliable plan.** ${message}\n\n${plan}`);
      }
      return 1;
    }
    console.log(`A deploy is changing what the plan reads (${urls}); waiting for it to finish.`);
    await sleep(30_000);
  }
}

if (process.argv[1] === fileURLToPath(import.meta.url)) {
  const [command, ...args] = process.argv.slice(2);
  const run =
    command === "await-ci"
      ? awaitCi
      : command === "deploy-activity"
        ? () => deployActivity(args)
        : undefined;
  if (run === undefined) {
    console.error("usage: workflow-runs.mjs await-ci | deploy-activity --since now|<ISO time>");
    process.exit(2);
  }
  try {
    process.exitCode = await run();
  } catch (error) {
    console.log(`::error::${error instanceof Error ? error.message : String(error)}`);
    process.exitCode = 1;
  }
}
