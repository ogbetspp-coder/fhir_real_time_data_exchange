import { execFileSync } from "node:child_process";
import { realpathSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

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

// The repository these scripts are part of: the code that runs. Not the current directory, which
// may be another repository (review of #148, round 2, L-2).
export const REPOSITORY_ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../..");

// Why a local run may not start from `cwd`, or undefined when it may. The run reads the mapping
// and the standards locks relative to the current directory (src/fhir/mapping.ts,
// src/fhir/standards-lock.ts), so from anywhere but the repository it would read another tree's.
export function outsideRepository(cwd: string = process.cwd()): string | undefined {
  const real = (directory: string): string => {
    try {
      return realpathSync(directory);
    } catch {
      return path.resolve(directory);
    }
  };
  return real(cwd) === real(REPOSITORY_ROOT)
    ? undefined
    : `run this from the repository's root (${REPOSITORY_ROOT}): it reads the mapping and the standards locks relative to the current directory`;
}

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

// The repository's HEAD, when its working tree is exactly HEAD: nothing changed, staged or
// untracked (what .gitignore excludes aside). Otherwise, or without git, undefined: the code that
// runs is then no commit, and the manifest says `development`. Git is asked about `root`, the
// repository of the code that runs, whatever the current directory is.
export function cleanHead(run: Git = git, root: string = REPOSITORY_ROOT): string | undefined {
  try {
    if (run(["-C", root, "status", "--porcelain", "--untracked-files=all"]).trim() !== "") {
      return undefined;
    }
    const head = run(["-C", root, "rev-parse", "HEAD"]).trim();
    return /^[0-9a-f]{40}$/.test(head) ? head : undefined;
  } catch {
    return undefined;
  }
}
