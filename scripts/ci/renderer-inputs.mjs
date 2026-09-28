import { execFileSync } from "node:child_process";
import { fileURLToPath } from "node:url";

// Whether a pull request changes anything the renderer image's checks read (CI's Renderer job,
// docs/design/authority-import-renderer.md): only then are the image built and the sweeps run.
// It fails closed: a path is an input unless it is under one of the few trees no renderer check
// reads, so a new file anywhere else runs every check; a push to main, a diff that cannot be
// read and an empty one run every check too. Prints `run=true` or `run=false` for
// $GITHUB_OUTPUT. Plain JavaScript, so it runs with node alone.
//
// usage (in the job): EVENT_NAME=pull_request node scripts/ci/renderer-inputs.mjs >> "$GITHUB_OUTPUT"

// Trees and files no renderer check reads (test/ci/renderer-inputs.test.ts holds every path the
// checks do read outside them): documentation, the Python deployables and the infrastructure,
// assistant settings, and Markdown anywhere.
export const NOT_INPUTS = [
  /^docs\//u,
  /^agent\//u,
  /^zone-a\//u,
  /^infra\//u,
  /^\.claude\//u,
  /^\.cursor\//u,
  /\.md$/u,
];

export function rendererInputsChanged(files) {
  return files.length === 0 || files.some((file) => !NOT_INPUTS.some((path) => path.test(file)));
}

// The files a pull request's merge commit changes against its base (its first parent), or
// undefined where that cannot be read.
function changedFiles() {
  try {
    execFileSync("git", ["rev-parse", "--verify", "--quiet", "HEAD^2"], { stdio: "ignore" });
    const out = execFileSync("git", ["diff", "--name-only", "-z", "HEAD^1", "HEAD"], {
      encoding: "utf8",
    });
    return out.split("\0").filter((file) => file !== "");
  } catch {
    return undefined;
  }
}

if (process.argv[1] === fileURLToPath(import.meta.url)) {
  const files = process.env.EVENT_NAME === "pull_request" ? changedFiles() : undefined;
  const run = files === undefined || rendererInputsChanged(files);
  console.error(
    files === undefined
      ? "renderer inputs: every check runs (not a pull request's merge commit)"
      : `renderer inputs: ${files.length} files changed; ${run ? "a renderer input among them, every check runs" : "none a renderer input, the checks are skipped"}`,
  );
  console.log(`run=${run}`);
}
