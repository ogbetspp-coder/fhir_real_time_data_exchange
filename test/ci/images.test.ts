import { existsSync, readFileSync } from "node:fs";

import { describe, expect, it } from "vitest";

// The worker's and the query service's image (audit B07, S-1 and S-5): one Dockerfile with a
// target for each; the pinned Node binary on a Debian base Dependabot refreshes; the runtime ADR
// 0003 pins asserted inside the image; no install scripts at build; and the standards lock the
// run manifest names shipped in the worker. CI's Images job builds them (scripts/ci/build-images.sh);
// these hold the files to that shape.

const dockerfile = readFileSync("Dockerfile", "utf8");
const cloudbuild = readFileSync("cloudbuild.images.yaml", "utf8");
const stages = [...dockerfile.matchAll(/^FROM (\S+)(?: AS (\S+))?$/gm)].map(([, image, name]) => ({
  image,
  name,
}));

// The builder cloudbuild.images.yaml holds: docker:29, which builds with BuildKit (audit B13).
const DOCKER_29 = "sha256:b7e5a7271b51a9fe4ee29ebaa98a06ecef86a5f0c51fd7a4bfab8e6a57294d94";

// Each `docker build` step's argument list.
function cloudbuildBuilds(): string[][] {
  const builds: string[][] = [];
  for (const [, args] of cloudbuild.matchAll(/args:\n((?:\s+- [^\n]*\n)+)/g)) {
    const list = [...(args ?? "").matchAll(/- (\S+)/g)].map((match) => match[1] ?? "");
    if (list[0] === "build") builds.push(list);
  }
  return builds;
}

function cloudbuildDockerfiles(): string[] {
  const files = new Set<string>();
  for (const list of cloudbuildBuilds()) {
    const at = list.indexOf("--file");
    files.add(at === -1 ? "Dockerfile" : (list[at + 1] ?? ""));
  }
  return [...files].sort();
}

describe("the worker and query image", () => {
  it("is one file with a build stage, a runtime, and a target per service, the worker last", () => {
    expect(existsSync("Dockerfile.query")).toBe(false);
    expect(stages.map(({ name }) => name)).toEqual(["build", "runtime", "query", "worker"]);
    expect(stages[2]?.image).toBe("runtime");
    expect(stages[3]?.image).toBe("runtime");
    const cmd = (target: string) =>
      new RegExp(`^FROM runtime AS ${target}\\n(?:#[^\\n]*\\n)*CMD (\\[[^\\n]*\\])$`, "m").exec(
        dockerfile,
      )?.[1];
    expect(cmd("worker")).toBe('["node", "dist/server.js"]');
    expect(cmd("query")).toBe('["node", "dist/query/server.js"]');
  });

  it("runs the pinned Node binary on a Debian base pinned by digest, which Dependabot moves", () => {
    expect(stages[0]?.image).toMatch(/^node:22\.22\.0-bookworm-slim@sha256:[0-9a-f]{64}$/);
    expect(stages[1]?.image).toMatch(/^debian:bookworm-slim@sha256:[0-9a-f]{64}$/);
    expect(dockerfile).toMatch(
      /^COPY --from=build \/usr\/local\/bin\/node \/usr\/local\/bin\/node$/m,
    );
    // Dependabot's docker ecosystem ignores only node's versions: debian's digest moves weekly.
    const dependabot = readFileSync(".github/dependabot.yml", "utf8");
    expect(dependabot).not.toMatch(/dependency-name:\s*"debian"/);
  });

  it("asserts Node, ICU and Unicode inside the image it ships, and a full-ICU build", () => {
    const assertion = /^RUN \["node", "-e", "([^\n]*)"\]$/m.exec(dockerfile)?.[1] ?? "";
    expect(assertion).toContain("node: '22.22.0', icu: '77.1', unicode: '16.0'");
    expect(assertion).toContain("process.exit(1)");
    expect(assertion).toContain("Intl.NumberFormat('de')");
    // Before any application file is copied: a wrong runtime fails the build, not a run.
    expect(dockerfile.indexOf(assertion)).toBeLessThan(
      dockerfile.indexOf("COPY --chown=node:node"),
    );
  });

  it("builds without install scripts and ships the standards lock the run manifest names", () => {
    expect(dockerfile).toMatch(
      /^RUN npm ci --no-audit --no-fund --ignore-scripts --engine-strict$/m,
    );
    expect(dockerfile).toMatch(
      /^COPY --chown=node:node fhir\/standards\.lock\.json \.\/fhir\/standards\.lock\.json$/m,
    );
    expect(dockerfile).toMatch(
      /^COPY --chown=node:node fhir\/validator-packages\.lock \.\/fhir\/validator-packages\.lock$/m,
    );
    expect(dockerfile).toMatch(/^USER node$/m);
  });

  it("leaves the renderer's code out of the build", () => {
    const build = JSON.parse(readFileSync("tsconfig.build.json", "utf8")) as { exclude: string[] };
    expect(build.exclude).toContain("src/render");
  });
});

describe("the Cloud Build configuration", () => {
  it("builds the worker and the query service from their targets", () => {
    const targets = cloudbuildBuilds().map((list) => list[list.indexOf("--target") + 1]);
    expect(targets).toContain("worker");
    expect(targets).toContain("query");
    expect(cloudbuild).not.toContain("Dockerfile.query");
  });

  // docker:29 since audit B13; until then the legacy docker:20.10.24, held until the move was
  // rehearsed on Cloud Build (audit B07, review round 1, M-1).
  it("builds with the one docker:29 builder, pinned by digest", () => {
    const builders = new Set(
      [...cloudbuild.matchAll(/gcr\.io\/cloud-builders\/docker@(sha256:[0-9a-f]{64})/g)].map(
        (match) => match[1],
      ),
    );
    expect([...builders]).toEqual([DOCKER_29]);
  });

  // BuildKit attaches provenance and SBOM attestations by default, which make a pushed tag an OCI
  // index; the deploy pins each tag's digest (scripts/gcp/deploy.sh, resolve_image_digest).
  it("builds every image as one image, with no attestation, in Cloud Build and in CI", () => {
    const builds = cloudbuildBuilds();
    expect(cloudbuildDockerfiles()).toEqual(["Dockerfile", "Dockerfile.validator"]);
    expect(builds).toHaveLength(3);
    for (const list of builds) {
      expect(list.slice(1, 3)).toEqual(["--provenance=false", "--sbom=false"]);
    }
    const script = readFileSync("scripts/ci/build-images.sh", "utf8");
    expect(script).not.toContain("DOCKER_BUILDKIT=0");
    const ciBuilds = script.match(/^docker build .*$/gm) ?? [];
    expect(ciBuilds).toHaveLength(3);
    for (const line of ciBuilds) {
      expect(line).toMatch(/^docker build --provenance=false --sbom=false /);
    }
  });

  // Under BuildKit, --cache-from reads only an image built with its cache metadata inline.
  it("pushes the validator's cache with its cache metadata inline, and builds from it", () => {
    const validator = (
      cloudbuildBuilds().find((list) => list.includes("Dockerfile.validator")) ?? []
    ).join(" ");
    expect(validator).toContain("--build-arg BUILDKIT_INLINE_CACHE=1");
    expect(validator).toMatch(/--cache-from \S+\/validator:buildcache /);
    expect(validator).toMatch(/--tag \S+\/validator:buildcache /);
  });

  // One judgement of the validator's offline start, in CI and in the image build (audit B08's
  // offlineStartVerdict), not a copy of it.
  it("judges the validator's offline start with the script Cloud Build runs", () => {
    const script = readFileSync("scripts/ci/build-images.sh", "utf8");
    expect(script).toMatch(/^node scripts\/ci\/validator-offline\.mjs "\$log"$/m);
    expect(cloudbuild).toMatch(/- scripts\/ci\/validator-offline\.mjs\n\s+- offline\.log\n/);
  });

  it("builds each service's target with B08's revision label", () => {
    for (const target of ["worker", "query"]) {
      expect(cloudbuild).toMatch(
        new RegExp(
          `- build\\n(?:\\s+- --(?:provenance|sbom)=false\\n)*\\s+- --target\\n\\s+- ${target}\\n\\s+- --label\\n\\s+- org\\.opencontainers\\.image\\.revision=\\$\\{_REVISION\\}\\n`,
        ),
      );
    }
  });
});
