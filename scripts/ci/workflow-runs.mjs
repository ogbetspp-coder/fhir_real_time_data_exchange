import { appendFileSync, existsSync, readFileSync, writeFileSync } from "node:fs";
import { setTimeout as sleep } from "node:timers/promises";
import { fileURLToPath } from "node:url";

// What the workflows ask GitHub about each other's runs (audit B15). Plain JavaScript with Node's
// built-ins only, so the deploy job, which can become the deployer, runs it without installing
// anything (test/ci/deploy-permissions.test.ts).
//
// await-ci: the deploy waits for CI's run on the commit it deploys, and goes on only when every
// job of it succeeded. Until then the deploy's own gate ran `npm run check` alone, so a push by an
// administrator, or a merge while Renderer or Official validation was red, deployed unattended.
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

// CI's jobs, by the names the checks carry; every one must have succeeded
// (test/ci/workflow-runs.test.ts holds this list to .github/workflows/ci.yml).
export const CI_JOBS = ["Check", "Official validation", "Renderer", "Images", "Zone A", "Agent"];

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

// "wait" while CI has no run for the commit or has not finished; "pass" only when the run and
// every job in it succeeded, each of CI_JOBS among them; "fail" otherwise, with the reason.
export function ciVerdict(run, jobs) {
  if (run === undefined) return { state: "wait", reason: "CI has no run for this commit yet" };
  if (run.status !== "completed") return { state: "wait", reason: `CI is ${run.status}` };
  if (run.conclusion !== "success") {
    return { state: "fail", reason: `CI concluded ${run.conclusion ?? "nothing"}` };
  }
  const failed = jobs.filter((job) => job.conclusion !== "success").map((job) => job.name);
  if (failed.length > 0)
    return { state: "fail", reason: `CI jobs not successful: ${failed.join(", ")}` };
  const missing = CI_JOBS.filter((name) => !jobs.some((job) => job.name === name));
  if (missing.length > 0)
    return { state: "fail", reason: `CI jobs missing: ${missing.join(", ")}` };
  return { state: "pass", reason: `CI succeeded: ${jobs.map((job) => job.name).join(", ")}` };
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

async function api(path) {
  const base = process.env.GITHUB_API_URL || "https://api.github.com";
  const token = process.env.GH_TOKEN;
  if (!token) throw new Error("GH_TOKEN is required");
  const response = await fetch(`${base}${path}`, {
    headers: {
      accept: "application/vnd.github+json",
      authorization: `Bearer ${token}`,
      "x-github-api-version": "2022-11-28",
    },
  });
  if (!response.ok) throw new Error(`GitHub API ${path.split("?")[0]} answered ${response.status}`);
  return await response.json();
}

function repository() {
  const repo = process.env.GITHUB_REPOSITORY ?? "";
  if (!/^[\w.-]+\/[\w.-]+$/.test(repo)) throw new Error("GITHUB_REPOSITORY is required");
  return repo;
}

async function jobsOf(repo, runId) {
  const { jobs } = await api(
    `/repos/${repo}/actions/runs/${runId}/jobs?filter=latest&per_page=100`,
  );
  return jobs;
}

async function awaitCi() {
  const repo = repository();
  const commit = process.env.COMMIT ?? "";
  if (!/^[0-9a-f]{40}$/.test(commit)) throw new Error("COMMIT must be a full commit SHA");
  const minutes = Number(process.env.AWAIT_CI_MINUTES ?? "75");
  const poll = Number(process.env.AWAIT_CI_POLL_SECONDS ?? "30") * 1000;
  const deadline = Date.now() + minutes * 60_000;
  for (;;) {
    const { workflow_runs: runs } = await api(
      `/repos/${repo}/actions/workflows/ci.yml/runs?head_sha=${commit}&event=push&branch=main&per_page=20`,
    );
    const run = ciRunFor(runs, commit);
    const verdict = ciVerdict(run, run?.status === "completed" ? await jobsOf(repo, run.id) : []);
    console.log(`CI on ${commit}: ${verdict.reason}${run ? ` (${run.html_url})` : ""}`);
    if (verdict.state === "pass") return 0;
    if (verdict.state === "fail") {
      console.log(`::error::The deploy waits for CI on this commit, and ${verdict.reason}.`);
      return 1;
    }
    if (Date.now() + poll > deadline) {
      console.log(
        `::error::CI on this commit did not finish within ${minutes} minutes: ${verdict.reason}.`,
      );
      return 1;
    }
    await sleep(poll);
  }
}

// The Deploy jobs of the deploy workflow's recent runs that may overlap [since, now]: every run not
// yet completed, and every run updated since `since`.
async function recentDeployJobs(repo, since) {
  const { workflow_runs: runs } = await api(
    `/repos/${repo}/actions/workflows/deploy.yml/runs?per_page=30`,
  );
  const candidates = runs.filter(
    (run) => run.status !== "completed" || Date.parse(run.updated_at) >= since,
  );
  const jobs = [];
  for (const run of candidates) {
    for (const job of await jobsOf(repo, run.id)) jobs.push({ ...job, run_url: run.html_url });
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
