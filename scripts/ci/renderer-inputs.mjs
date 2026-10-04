import { execFileSync } from "node:child_process";
import { appendFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

// Whether a pull request changes anything the renderer image's checks read (CI's Renderer job,
// docs/design/authority-import-renderer.md): only then are the image built and the sweeps run.
// It fails closed: a path is an input unless it is under one of the few trees no renderer check
// reads, so a new file anywhere else runs every check; a push to main, a diff that cannot be
// read and an empty one run every check too. Prints `run=true` or `run=false` for
// $GITHUB_OUTPUT. Plain JavaScript, so it runs with node alone.
//
// usage (in the job): EVENT_NAME=pull_request BASE_SHA=<base.sha> HEAD_SHA=<head.sha> \
//   node scripts/ci/renderer-inputs.mjs >> "$GITHUB_OUTPUT"

// Trees and files no renderer check reads (test/ci/renderer-inputs.test.ts holds every path the
// checks do read outside them): documentation, the Python deployables, the label reader, the infrastructure,
// assistant settings, and Markdown anywhere.
export const NOT_INPUTS = [
  /^docs\//u,
  /^agent\//u,
  /^zone-a\//u,
  /^label-docx-reader\//u,
  /^infra\//u,
  /^\.claude\//u,
  /^\.cursor\//u,
  /\.md$/u,
];

export function rendererInputsChanged(files) {
  return files.length === 0 || files.some((file) => !NOT_INPUTS.some((path) => path.test(file)));
}

// The diff of a pull request's merge commit against its base (its first parent): every path it
// adds, changes or deletes. Renames are not detected, so a file moved out of an input tree (into
// docs/, say) is listed at its old path as well as its new one.
export const DIFF_ARGS = ["diff", "--no-renames", "--name-only", "-z", "HEAD^1", "HEAD"];

// The files that diff lists, or undefined where it cannot be read. The merge commit must be the one
// the event describes: its parents the pull request's base and head commits (BASE_SHA and
// HEAD_SHA, from github.event.pull_request), so a merge commit GitHub recomputed after the event,
// or anything else checked out in its place, is read as unknown and every check runs (audit B07,
// carried from B11's review).
function changedFiles(env) {
  try {
    const commit = (ref) =>
      execFileSync("git", ["rev-parse", "--verify", "--quiet", `${ref}^{commit}`], {
        encoding: "utf8",
      }).trim();
    if (commit("HEAD^1") !== env.BASE_SHA || commit("HEAD^2") !== env.HEAD_SHA) return undefined;
    const out = execFileSync("git", DIFF_ARGS, { encoding: "utf8" });
    return out.split("\0").filter((file) => file !== "");
  } catch {
    return undefined;
  }
}

if (process.argv[1] === fileURLToPath(import.meta.url)) {
  const files = process.env.EVENT_NAME === "pull_request" ? changedFiles(process.env) : undefined;
  const run = files === undefined || rendererInputsChanged(files);
  console.error(
    files === undefined
      ? "renderer inputs: every check runs (not a pull request's merge commit)"
      : `renderer inputs: ${files.length} files changed; ${run ? "a renderer input among them, every check runs" : "none a renderer input, the checks are skipped"}`,
  );
  // A skipped run is shown on the job's summary, not only in its log.
  if (!run && process.env.GITHUB_STEP_SUMMARY !== undefined) {
    appendFileSync(
      process.env.GITHUB_STEP_SUMMARY,
      `Renderer checks skipped: no renderer input changed (${files?.length ?? 0} files, each documentation, a Python deployable, infrastructure, assistant settings or Markdown).\n`,
    );
  }
  console.log(`run=${run}`);
}
