import { spawnSync } from "node:child_process";
import { chmodSync, existsSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";

import { afterEach, describe, expect, it } from "vitest";

// The digest each deployed image is pinned by (scripts/gcp/deploy.sh, resolve_image_digest). Since
// audit B13 the images are built by docker:29 with BuildKit, which can push a tag as an OCI index
// (its attestations do, unless turned off). A registry asked only for single-image manifest types
// does not answer with the digest of a tag that is an index, so the deploy would stop there. These
// run the real function against a stand-in registry.

const deploy = readFileSync("scripts/gcp/deploy.sh", "utf8");
const shells = ["bash", ...(existsSync("/bin/bash") ? ["/bin/bash"] : [])];
const dirs: string[] = [];

afterEach(() => {
  for (const dir of dirs.splice(0)) rmSync(dir, { recursive: true, force: true });
});

const INDEX = "application/vnd.oci.image.index.v1+json";
const LIST = "application/vnd.docker.distribution.manifest.list.v2+json";
const DOCKER = "application/vnd.docker.distribution.manifest.v2+json";
const DIGEST = `sha256:${"a".repeat(64)}`;

// The stand-in registry holds the tag as `stored` and answers HEAD with its digest only when the
// Accept header names that type, as a registry does; otherwise 404.
function resolve(shell: string, stored: string) {
  const body = /^resolve_image_digest\(\) \{\n[\s\S]*?^\}$/m.exec(deploy)?.[0];
  if (body === undefined) throw new Error("resolve_image_digest not found in deploy.sh");
  const dir = mkdtempSync(path.join(tmpdir(), "image-digest-"));
  dirs.push(dir);
  const stubs: Record<string, string> = {
    gcloud: `case "$*" in *print-access-token*) printf ya29.never-in-argv ;; *) exit 2 ;; esac`,
    curl: `printf '%s\\n' "$*" >>"${dir}/calls"
accept=""
while [ $# -gt 0 ]; do
  case "$1" in
    --header) case "$2" in Accept:*) accept="$2" ;; esac; shift 2 ;;
    *) shift ;;
  esac
done
case "$accept" in
  *"${stored}"*) printf 'HTTP/2 200\\r\\ndocker-content-digest: ${DIGEST}\\r\\ncontent-type: ${stored}\\r\\n\\r\\n' ;;
  *) echo "curl: (22) The requested URL returned error: 404" >&2; exit 22 ;;
esac`,
  };
  for (const [name, text] of Object.entries(stubs)) {
    writeFileSync(path.join(dir, name), `#!/usr/bin/env bash\n${text}\n`);
    chmodSync(path.join(dir, name), 0o755);
  }
  const result = spawnSync(
    shell,
    [
      "-c",
      `set -euo pipefail
source scripts/gcp/common.sh
PROJECT_ID=p REGION=europe-west4 REPOSITORY_ID=ema-flow-images
${body}
resolve_image_digest worker abc123`,
    ],
    { encoding: "utf8", env: { PATH: `${dir}:${process.env.PATH ?? ""}` } },
  );
  return {
    status: result.status,
    stdout: result.stdout,
    calls: existsSync(path.join(dir, "calls")) ? readFileSync(path.join(dir, "calls"), "utf8") : "",
  };
}

describe("the deploy's image digest lookup", () => {
  it.each(shells.flatMap((shell) => [INDEX, LIST, DOCKER].map((type) => [shell, type])))(
    "under %s, answers a tag stored as %s with the digest the registry names",
    (shell, type) => {
      const result = resolve(shell, type);
      expect([result.status, result.stdout]).toEqual([0, DIGEST]);
      expect(result.calls).toContain(
        "https://europe-west4-docker.pkg.dev/v2/p/ema-flow-images/worker/manifests/abc123",
      );
      expect(result.calls).not.toContain("ya29.never-in-argv");
    },
  );
});
