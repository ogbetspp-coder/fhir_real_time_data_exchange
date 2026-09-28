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

// What BuildKit accepts and the legacy builder does not: the syntax directive, RUN flags, heredocs,
// the newer COPY and ADD flags, and the automatic platform arguments. Continuation lines are joined
// first, as the builder joins them.
function buildkitOnly(text: string): string[] {
  // Directives are read from the raw file; instructions after continuation lines are joined, as
  // the builder joins them, and an ONBUILD trigger is judged as the instruction it defers.
  const directives = text.split(/\r?\n/).filter((line) => /^\s*#/.test(line));
  const instructions = text
    .replace(/\\\s*\n\s*/g, " ")
    .split(/\r?\n/)
    .filter((line) => !/^\s*#/.test(line))
    .map((line) => line.replace(/^\s*ONBUILD\s+/i, ""));
  const directiveRules: [string, RegExp][] = [
    ["# syntax= directive", /^\s*#\s*syntax\s*=/i],
    // The legacy builder reads it; it is refused because it changes the line continuation this
    // guard, the pin readers and check-dockerfiles.mjs assume.
    ["# escape= directive", /^\s*#\s*escape\s*=/i],
  ];
  const rules: [string, RegExp][] = [
    [
      "RUN --mount, --network, --security or --device",
      /^\s*RUN\s+(?:--\S+\s+)*--(?:mount|network|security|device)\b/i,
    ],
    ["a heredoc", /^\s*(?:RUN|COPY|ADD)\b.*<<-?\s*["']?\w+/i],
    [
      "COPY or ADD --chmod, --link, --parents or --exclude",
      /^\s*(?:COPY|ADD)\b.*\s--(?:chmod|link|parents|exclude)\b/i,
    ],
    [
      "ADD --checksum, --keep-git-dir or --unpack",
      /^\s*ADD\b.*\s--(?:checksum|keep-git-dir|unpack)\b/i,
    ],
    ["HEALTHCHECK --start-interval", /^\s*HEALTHCHECK\b.*\s--start-interval\b/i],
    ["an automatic platform argument", /\$\{?(?:BUILD|TARGET)(?:PLATFORM|OS|ARCH|VARIANT)\b/],
    // ${V#…}, ${V%…} and ${V/…} are BuildKit's; the legacy builder substitutes only $V, ${V},
    // ${V:-…} and ${V:+…}. RUN, CMD and ENTRYPOINT are left to the shell, which has its own.
    [
      "a pattern expansion in an instruction the builder substitutes",
      /^\s*(?!RUN\b|CMD\b|ENTRYPOINT\b|HEALTHCHECK\b)[A-Z]+\b.*\$\{\w+(?:#|%|\/)/i,
    ],
  ];
  return [
    ...directiveRules.filter(([, rule]) => directives.some((line) => rule.test(line))),
    ...rules.filter(([, rule]) => instructions.some((line) => rule.test(line))),
  ].map(([name]) => name);
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
  // off by name, not by the builder's default (audit B07 follow-up, Low-1).
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
    ["a heredoc COPY", "FROM x\nCOPY <<-EOT /a\nx\nEOT\n"],
    ["COPY --chmod", "FROM x\nCOPY --chmod=755 a /a\n"],
    ["COPY --link", "FROM x\nCOPY --link a /a\n"],
    ["COPY --parents", "FROM x\nCOPY --parents a/b /c\n"],
    ["COPY --exclude", "FROM x\nCOPY --exclude=*.md . /c\n"],
    ["ADD --checksum", "FROM x\nADD --checksum=sha256:00 https://e/x /x\n"],
    ["an automatic platform argument", "FROM --platform=$BUILDPLATFORM x\n"],
    ["a line continued into a mount", "FROM x\nRUN \\\n    --mount=type=secret,id=a true\n"],
    // Review round 2 (audit B07 follow-up, Low-4).
    ["an ONBUILD RUN --mount", "FROM x\nONBUILD RUN --mount=type=cache,target=/c true\n"],
    ["an ONBUILD COPY --chmod", "FROM x\nONBUILD COPY --chmod=755 a /a\n"],
    ["ADD --unpack", "FROM x\nADD --unpack=true https://e/x.tgz /x\n"],
    ["RUN --device", "FROM x\nRUN --device=/dev/fuse true\n"],
    ["HEALTHCHECK --start-interval", "FROM x\nHEALTHCHECK --start-interval=1s CMD true\n"],
    ["a suffix-removing expansion", "FROM x\nARG V=a.tgz\nCOPY ${V%.tgz} /a\n"],
    ["a prefix-removing expansion", "FROM x\nARG V=a/b\nENV W=${V#a/}\n"],
    ["a replacing expansion", "FROM x\nARG V=a\nWORKDIR /${V/a/b}\n"],
    ["an escape directive", "# escape=`\nFROM x\n"],
  ])("refuses %s while the builder is legacy", (_, text) => {
    expect(buildkitOnly(text)).not.toEqual([]);
  });

  it.each([
    ["a shell expansion inside RUN", 'FROM x\nRUN v=a.tgz; echo "${v%.tgz}"\n'],
    ["the legacy default forms", "FROM x\nARG V\nENV W=${V:-a} X=${V:+b}\nCOPY $V ${V} /a\n"],
    ["COPY --chown and --from", "FROM x AS b\nFROM x\nCOPY --chown=a:a --from=b /a /a\n"],
    ["an ordinary comment", "# the base\nFROM x\n"],
  ])("accepts %s, which the legacy builder builds", (_, text) => {
    expect(buildkitOnly(text)).toEqual([]);
  });
});
