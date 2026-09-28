import { execFileSync } from "node:child_process";

// What a run on this machine may say produced it (review of #148, M1). scripts/dev/run-pipeline.ts
// takes its configuration from the deployed worker, and the worker's configuration names the
// deployed commit and images (GIT_COMMIT, IMAGE_DIGEST, VALIDATOR_IMAGE_DIGEST) and its Cloud Run
// revision (K_REVISION). The run manifest reads its `runtime` from that configuration
// (src/pipeline.ts, manifestRuntime), so a local `--persist` run would sign, with the deployment's
// HSM key, a manifest naming code and images that did not run. None of them is copied: the local
// run is `development`, except the commit when this checkout is exactly one, clean, commit.

export const RUNTIME_VARIABLES = [
  "GIT_COMMIT",
  "IMAGE_DIGEST",
  "VALIDATOR_IMAGE_DIGEST",
  "WORKFLOW_REVISION",
  "K_REVISION",
] as const;

// The deployed environment without the variables that name what ran, and with GIT_COMMIT set to
// `localCommit` when there is one.
export function localEnvironment(
  deployed: ReadonlyMap<string, string>,
  localCommit: string | undefined,
): Record<string, string> {
  const environment: Record<string, string> = Object.fromEntries(
    [...deployed].filter(([name]) => !(RUNTIME_VARIABLES as readonly string[]).includes(name)),
  );
  if (localCommit !== undefined) environment.GIT_COMMIT = localCommit;
  return environment;
}

type Git = (args: string[]) => string;

function git(args: string[]): string {
  return execFileSync("git", args, { encoding: "utf8", stdio: ["ignore", "pipe", "ignore"] });
}

// HEAD, when the working tree is exactly HEAD: nothing changed, staged or untracked (what
// .gitignore excludes aside). Otherwise, or without git, undefined: the code that runs is then no
// commit, and the manifest says `development`.
export function cleanHead(run: Git = git): string | undefined {
  try {
    if (run(["status", "--porcelain", "--untracked-files=all"]).trim() !== "") return undefined;
    const head = run(["rev-parse", "HEAD"]).trim();
    return /^[0-9a-f]{40}$/.test(head) ? head : undefined;
  } catch {
    return undefined;
  }
}
