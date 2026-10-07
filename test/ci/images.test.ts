import { existsSync, readdirSync, readFileSync } from "node:fs";

import { describe, expect, it } from "vitest";

import { instructions } from "../../scripts/ci/dockerfile.mjs";

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

// The builder cloudbuild.images.yaml holds (Docker 20.10.24), and the Dockerfiles it builds.
const LEGACY_BUILDER = "sha256:001fb4a870a84485cf198c80002f7af42f6456f2dbc261d70a0b5f0111470df4";

// Each step of cloudbuild.images.yaml: its id, image, env list and argument list.
function cloudbuildSteps(): { id: string; name: string; env: string[]; args: string[] }[] {
  return cloudbuild
    .split(/\n(?= {2}- id: )/)
    .slice(1)
    .map((text) => {
      const field = (key: string) => new RegExp(`^ {4}${key}: (.*)$`, "m").exec(text)?.[1] ?? "";
      const argsBlock = /^ {4}args:\n((?: {6}- [^\n]*\n?)+)/m.exec(text)?.[1] ?? "";
      return {
        id: /^ {2}- id: (\S+)/.exec(text)?.[1] ?? "",
        name: field("name"),
        env: field("env") === "" ? [] : (JSON.parse(field("env")) as string[]),
        args: [...argsBlock.matchAll(/^ {6}- (.*)$/gm)].map((match) => match[1] ?? ""),
      };
    });
}

function cloudbuildDockerfiles(): string[] {
  const files = new Set<string>();
  for (const [, args] of cloudbuild.matchAll(/args:\n((?:\s+- [^\n]*\n)+)/g)) {
    const list = [...(args ?? "").matchAll(/- (\S+)/g)].map((match) => match[1]);
    if (list[0] !== "build") continue;
    const at = list.indexOf("--file");
    files.add(at === -1 ? "Dockerfile" : (list[at + 1] ?? ""));
  }
  return [...files].sort();
}

// The BuildKit-only constructs a contributor is likely to copy in from current Docker examples,
// which the held legacy builder refuses or misreads. A short list of plausible mistakes, read
// through the shared reader (scripts/ci/dockerfile.mjs); anything rarer is for review (O3).
const BUILDKIT_ONLY: [string, RegExp][] = [
  ["RUN --mount or --network", /^\s*RUN\s+(?:--\S+\s+)*--(?:mount|network)\b/i],
  ["a heredoc", /^\s*(?:RUN|COPY)\b.*<<-?\s*["']?\w+/i],
  ["COPY or ADD --chmod or --link", /^\s*(?:COPY|ADD)\b.*\s--(?:chmod|link)\b/i],
  ["ADD --checksum", /^\s*ADD\b.*\s--checksum\b/i],
  ["an automatic platform argument", /\$\{?(?:BUILD|TARGET)PLATFORM\b/],
];
function buildkitOnly(text: string): string[] {
  const lines = instructions(text).map((instruction) => instruction.text);
  return [
    ...(/^\s*#\s*syntax\s*=/im.test(text) ? ["a syntax directive"] : []),
    ...BUILDKIT_ONLY.filter(([, rule]) => lines.some((line) => rule.test(line))).map(([n]) => n),
  ];
}

describe("the worker and query image", () => {
  it("is one file with build stages, a runtime, and a target per service, the worker last", () => {
    expect(existsSync("Dockerfile.query")).toBe(false);
    expect(stages.map(({ name }) => name)).toEqual([
      "build",
      "uv",
      "python",
      "runtime",
      "query",
      "signer",
      "worker",
    ]);
    expect(stages.slice(4).map(({ image }) => image)).toEqual(["runtime", "runtime", "runtime"]);
    // The query service's and the signer's target is the runtime and their command; the worker's
    // adds the recompute's Python first (below).
    const cmd = (target: string) =>
      new RegExp(
        `^FROM runtime AS ${target}\\n(?:(?:#|COPY --from=python |COPY qrd/|ENV |    |RUN \\[")[^\\n]*\\n)*CMD (\\[[^\\n]*\\])$`,
        "m",
      ).exec(dockerfile)?.[1];
    expect(cmd("worker")).toBe('["node", "dist/server.js"]');
    expect(cmd("query")).toBe('["node", "dist/query/server.js"]');
    expect(cmd("signer")).toBe('["node", "dist/signer/server.js"]');
  });

  it("runs the pinned Node binary on a Debian base pinned by digest, which Dependabot moves", () => {
    expect(stages[0]?.image).toMatch(/^node:22\.22\.0-bookworm-slim@sha256:[0-9a-f]{64}$/);
    expect(stages[3]?.image).toMatch(/^debian:bookworm-slim@sha256:[0-9a-f]{64}$/);
    // The recompute's Python is built on the same Debian, by the uv CI pins.
    expect(stages[2]?.image).toBe(stages[3]?.image);
    expect(stages[1]?.image).toMatch(/^ghcr\.io\/astral-sh\/uv:0\.12\.17@sha256:[0-9a-f]{64}$/);
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

  // docs/design/certified-word-import.md, D2: the worker alone carries Python 3.14, the zone-a and
  // label-docx packages from zone-a/uv.lock, not editable, and the files zone_a.recompute reads,
  // and names them to the gate; the query service's and the signer's images carry none of it.
  it("gives the worker, and only the worker, the recompute's Python and files", () => {
    expect(dockerfile).toMatch(/^RUN uv python install 3\.14\.\d+$/m);
    expect(dockerfile).toMatch(
      /^RUN uv sync --locked --no-dev --no-editable --python 3\.14\.\d+ --project zone-a$/m,
    );
    const worker = dockerfile.slice(dockerfile.indexOf("FROM runtime AS worker"));
    const others = dockerfile.slice(
      dockerfile.indexOf("FROM runtime AS query"),
      dockerfile.indexOf("FROM runtime AS worker"),
    );
    for (const line of [
      "COPY --from=python /opt/python /opt/python",
      "COPY --from=python /opt/zone-a /opt/zone-a",
      "COPY qrd/registry ./qrd/registry",
    ]) {
      expect([line, worker.includes(line), others.includes(line)]).toEqual([line, true, false]);
    }
    expect(worker).toMatch(
      /^ENV RECOMPUTE_PYTHON=\/opt\/zone-a\/bin\/python \\\n {4}ZONE_A_ROOT=\/app$/m,
    );
    expect(worker).toContain("unicodedata.unidata_version");
    expect(readFileSync(".dockerignore", "utf8")).toMatch(/^!zone-a\/uv\.lock$/m);
    // CI pins the same uv, and every workflow's Python is the image's, patch and all (review of
    // #196: CI's "3.14" had moved to 3.14.8 while the image ran 3.14.7).
    expect(readFileSync(".github/workflows/ci.yml", "utf8")).toContain('version: "0.12.17"');
    const image = /^RUN uv python install (\S+)$/m.exec(dockerfile)?.[1];
    expect(dockerfile).toContain(`--python ${image ?? ""} --project zone-a`);
    const workflows = readdirSync(".github/workflows").flatMap((file) =>
      [
        ...readFileSync(`.github/workflows/${file}`, "utf8").matchAll(
          /^\s+python-version: "([^"]+)"$/gm,
        ),
      ].map(([, version]) => [file, version]),
    );
    expect(workflows.length).toBeGreaterThanOrEqual(5);
    for (const [file, version] of workflows) expect([file, version]).toEqual([file, image]);
  });

  it("leaves the renderer's code out of the build", () => {
    const build = JSON.parse(readFileSync("tsconfig.build.json", "utf8")) as { exclude: string[] };
    expect(build.exclude).toContain("src/render");
  });
});

describe("the Cloud Build configuration", () => {
  it("builds the worker and the query service from their targets", () => {
    expect(cloudbuild).toMatch(/- build\n\s+- --target\n\s+- worker\n/);
    expect(cloudbuild).toMatch(/- build\n\s+- --target\n\s+- query\n/);
    expect(cloudbuild).not.toContain("Dockerfile.query");
  });

  // Held on the legacy builder until its move is rehearsed (audit B07, review round 1, M-1; B13).
  it("builds with the one held, known-good docker builder", () => {
    const builders = new Set(
      [...cloudbuild.matchAll(/gcr\.io\/cloud-builders\/docker@(sha256:[0-9a-f]{64})/g)].map(
        (match) => match[1],
      ),
    );
    expect([...builders]).toEqual([LEGACY_BUILDER]);
  });

  // Every step runs one of two pinned images, and every build runs the held builder with BuildKit
  // off by name, not by the builder's default (audit B07 follow-up, Low-1). What it reads, and so
  // what it holds: the text of cloudbuild.images.yaml, split at each top-level `- id:`; in each
  // step its `name:`, a one-line flow-style `env: [...]` and the `- ` items of its `args:`. A
  // build is a step whose first argument is `build`. It does not parse YAML (an anchor, a
  // block-style env list or a quoted key is not read), and it does not read a docker build run
  // from inside another step's script (`entrypoint: bash` with `-c`): images:check pins every
  // step's image, and review holds the rest.
  it("runs every step in the held builder or the pinned node image, and builds with BuildKit off", () => {
    const nodeImage = /^FROM (node:\S+@sha256:[0-9a-f]{64}) AS build$/m.exec(dockerfile)?.[1];
    const steps = cloudbuildSteps();
    expect(steps.length).toBeGreaterThanOrEqual(6);
    for (const step of steps) {
      expect([step.id, [`gcr.io/cloud-builders/docker@${LEGACY_BUILDER}`, nodeImage]]).toEqual([
        step.id,
        expect.arrayContaining([step.name]),
      ]);
      if (step.args[0] === "build") {
        expect([step.id, step.name, step.env]).toEqual([
          step.id,
          `gcr.io/cloud-builders/docker@${LEGACY_BUILDER}`,
          ["DOCKER_BUILDKIT=0"],
        ]);
      }
    }
    expect(steps.filter((step) => step.args[0] === "build").map(({ id }) => id)).toEqual([
      "build-app-image",
      "build-validator-image",
      "build-query-image",
      "build-signer-image",
    ]);
  });

  it("builds only what the legacy builder can build, and CI builds it the same way", () => {
    const built = cloudbuildDockerfiles();
    expect(built).toEqual(["Dockerfile", "Dockerfile.validator"]);
    for (const file of built)
      expect([file, buildkitOnly(readFileSync(file, "utf8"))]).toEqual([file, []]);
    expect(readFileSync("scripts/ci/build-images.sh", "utf8")).toMatch(
      /^export DOCKER_BUILDKIT=0$/m,
    );
  });

  // docs/design/certified-word-import.md, D2: the deploy rebuilds the worker in Cloud Build, so the
  // image that is pushed is proven there, by the script CI's Images job runs (review of #196).
  it("runs the recompute smoke on the worker it built, before anything is pushed", () => {
    const smoke = cloudbuildSteps().find(({ id }) => id === "worker-recompute-smoke");
    expect(smoke?.args).toEqual([
      "scripts/ci/worker-recompute-smoke.sh",
      "${_REGION}-docker.pkg.dev/${PROJECT_ID}/${_REPOSITORY}/worker:${_IMAGE_TAG}",
    ]);
    expect(cloudbuild).toMatch(
      /- id: worker-recompute-smoke\n(?: {4}.*\n)*? {4}waitFor: \["build-app-image"\]\n {4}entrypoint: bash\n/,
    );
    expect(readFileSync("scripts/ci/build-images.sh", "utf8")).toMatch(
      /^bash scripts\/ci\/worker-recompute-smoke\.sh ema-flow\/worker:ci$/m,
    );
  });

  it("expects the validator image to run the command Dockerfile.validator declares", () => {
    const script = readFileSync("scripts/ci/build-images.sh", "utf8");
    const expected = /^expect_config ema-flow\/validator:ci validator '(\[.*\])'$/m.exec(script);
    const declared = /^CMD (\[.*\])$/m.exec(readFileSync("Dockerfile.validator", "utf8"));
    expect(JSON.parse(expected?.[1] ?? "null")).toEqual(JSON.parse(declared?.[1] ?? "[]"));
  });

  // One judgement of the validator's offline start, in CI and in the image build (audit B08's
  // offlineStartVerdict), not a copy of it.
  it("judges the validator's offline start with the script Cloud Build runs", () => {
    const script = readFileSync("scripts/ci/build-images.sh", "utf8");
    expect(script).toMatch(/^node scripts\/ci\/validator-offline\.mjs "\$log"$/m);
    expect(cloudbuild).toMatch(/- scripts\/ci\/validator-offline\.mjs\n\s+- offline\.log\n/);
  });

  it("builds each service's target with B08's revision label", () => {
    for (const target of ["worker", "query", "signer"]) {
      expect(cloudbuild).toMatch(
        new RegExp(
          `- build\\n\\s+- --target\\n\\s+- ${target}\\n\\s+- --label\\n\\s+- org\\.opencontainers\\.image\\.revision=\\$\\{_REVISION\\}\\n`,
        ),
      );
    }
  });

  it.each([
    ["a syntax directive", "# syntax=docker/dockerfile:1\nFROM x\n"],
    ["a cache mount", "FROM x\nRUN --mount=type=cache,target=/root/.npm npm ci\n"],
    ["a RUN network flag", "FROM x\nRUN --network=none true\n"],
    ["a heredoc", "FROM x\nRUN <<EOF\ntrue\nEOF\n"],
    ["COPY --chmod", "FROM x\nCOPY --chmod=755 a /a\n"],
    ["COPY --link", "FROM x\nCOPY --link a /a\n"],
    ["ADD --checksum", "FROM x\nADD --checksum=sha256:00 https://e/x /x\n"],
    ["an automatic platform argument", "FROM --platform=$BUILDPLATFORM x\n"],
    ["a line continued into a mount", "FROM x\nRUN \\\n    --mount=type=secret,id=a true\n"],
  ])("refuses %s while the builder is legacy", (_, text) => {
    expect(buildkitOnly(text)).not.toEqual([]);
  });

  it.each([
    ["COPY --chown and --from", "FROM x AS b\nFROM x\nCOPY --chown=a:a --from=b /a /a\n"],
    ["an ordinary comment", "# the base\nFROM x\n"],
  ])("accepts %s, which the legacy builder builds", (_, text) => {
    expect(buildkitOnly(text)).toEqual([]);
  });
});
