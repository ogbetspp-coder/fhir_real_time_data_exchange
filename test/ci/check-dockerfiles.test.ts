import { spawnSync } from "node:child_process";
import { mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";

import { afterEach, describe, expect, it } from "vitest";

// The image-pinning gate (`npm run images:check`) is run here against fixture Dockerfiles, so
// the check is held to the references it claims to cover rather than only to the repository's
// own three files — which all pass, and would pass a check that scanned nothing.

const SCRIPT = path.resolve("scripts/ci/check-dockerfiles.mjs");
const NODE_DIGEST = `sha256:${"1c".repeat(32)}`;
const OTHER_DIGEST = `sha256:${"2d".repeat(32)}`;
const PINNED_NODE = `node:22.14.0-bookworm-slim@${NODE_DIGEST}`;

const directories: string[] = [];

afterEach(() => {
  for (const directory of directories.splice(0))
    rmSync(directory, { recursive: true, force: true });
});

type Result = { status: number; stdout: string; stderr: string };

// Writes the given Dockerfiles into a fresh directory and runs the real script over it.
function check(files: Record<string, string>): Result {
  const directory = mkdtempSync(path.join(tmpdir(), "ema-flow-images-"));
  directories.push(directory);
  for (const [name, content] of Object.entries(files)) {
    writeFileSync(path.join(directory, name), content, "utf8");
  }
  const run = spawnSync(process.execPath, [SCRIPT, directory], { encoding: "utf8" });
  return { status: run.status ?? -1, stdout: run.stdout, stderr: run.stderr };
}

describe("Dockerfile image pinning gate", () => {
  it("passes a file whose every image reference is a digest or a declared stage", () => {
    const result = check({
      Dockerfile: [
        `FROM ${PINNED_NODE} AS build`,
        "RUN npm ci",
        `FROM ${PINNED_NODE} AS runtime`,
        "COPY --chown=node:node --from=build /app/dist ./dist",
        "COPY --from=0 /app/package.json ./",
        "RUN --mount=type=cache,target=/root/.npm npm ci",
        "RUN --mount=type=bind,from=build,source=/app,target=/build ls /build",
      ].join("\n"),
    });

    expect([result.status, result.stderr]).toEqual([0, ""]);
    expect(result.stdout).toContain("pinned by digest");
  });

  it("fails an unpinned FROM", () => {
    const result = check({ Dockerfile: "FROM node:22-slim AS build\nRUN npm ci\n" });

    expect(result.status).toBe(1);
    expect(result.stderr).toContain("Dockerfile:1: FROM node:22-slim is not pinned");
  });

  it("fails an unpinned image pulled through COPY --from", () => {
    const result = check({
      Dockerfile: [`FROM ${PINNED_NODE} AS build`, "COPY --from=node:latest /usr/bin/node ./"].join(
        "\n",
      ),
    });

    expect(result.status).toBe(1);
    expect(result.stderr).toContain("Dockerfile:2: COPY --from node:latest is not pinned");
  });

  it("fails an unpinned image pulled through ADD --from", () => {
    const result = check({
      Dockerfile: [`FROM ${PINNED_NODE} AS build`, "ADD --from=alpine:latest /etc/passwd ./"].join(
        "\n",
      ),
    });

    expect(result.status).toBe(1);
    expect(result.stderr).toContain("Dockerfile:2: COPY --from alpine:latest is not pinned");
  });

  it("fails an unpinned image mounted into a RUN", () => {
    const result = check({
      Dockerfile: [
        `FROM ${PINNED_NODE} AS build`,
        "RUN --mount=type=bind,from=alpine:latest,source=/bin,target=/mnt /mnt/sh -c true",
      ].join("\n"),
    });

    expect(result.status).toBe(1);
    expect(result.stderr).toContain(
      "Dockerfile:2: RUN --mount from alpine:latest is not pinned by @sha256 digest",
    );
  });

  it("fails an unpinned reference written after a line continuation", () => {
    const result = check({
      Dockerfile: [
        `FROM ${PINNED_NODE} AS build`,
        "RUN \\",
        "  --mount=type=bind,from=busybox:latest,target=/mnt \\",
        "  /mnt/bin/true",
      ].join("\n"),
    });

    expect(result.status).toBe(1);
    expect(result.stderr).toContain("Dockerfile:2: RUN --mount from busybox:latest is not pinned");
  });

  // Joined as the builder joins them (audit B07 follow-up, review L3-b): no space added, and a
  // comment or a blank line inside a continuation does not end the instruction.
  it.each([
    [
      "a flag split across lines",
      ["RUN --mo\\", "unt=type=bind,from=busybox:latest,target=/mnt true"],
    ],
    [
      "a mount after a comment inside a continuation",
      ["RUN \\", "# the mount", "  --mount=type=bind,from=busybox:latest,target=/mnt true"],
    ],
    [
      "a mount after a blank line inside a continuation",
      ["RUN \\", "", "  --mount=type=bind,from=busybox:latest,target=/mnt true"],
    ],
  ])("fails an unpinned mount reached through %s", (_, lines) => {
    const result = check({ Dockerfile: [`FROM ${PINNED_NODE} AS build`, ...lines].join("\n") });

    expect(result.status).toBe(1);
    expect(result.stderr).toContain("Dockerfile:2: RUN --mount from busybox:latest is not pinned");
  });

  it("fails a reference to a stage declared below it, because Docker resolves it as an image", () => {
    // Docker only lets `COPY --from` name a stage declared above it, so `runtime` here is not
    // the stage two lines down: it is an image reference, unpinned. Treating it as a stage
    // would let any unpinned image be shielded by choosing a name the file declares later.
    const result = check({
      Dockerfile: [
        `FROM ${PINNED_NODE} AS build`,
        "COPY --from=runtime /app/package.json ./",
        `FROM ${PINNED_NODE} AS runtime`,
      ].join("\n"),
    });

    expect(result.status).toBe(1);
    expect(result.stderr).toContain("COPY --from runtime is not pinned");
  });

  it("passes a stage referenced after the instruction that declares it", () => {
    const result = check({
      Dockerfile: [
        `FROM ${PINNED_NODE} AS build`,
        `FROM ${PINNED_NODE} AS runtime`,
        "COPY --from=build /app/package.json ./",
      ].join("\n"),
    });

    expect([result.status, result.stderr]).toEqual([0, ""]);
  });

  it("fails two stages in one file pinned to different node digests", () => {
    // Keying the digest by file would let the second FROM overwrite the first, hiding exactly
    // the drift ADR 0003 cares about: a build stage moved to a different Unicode database.
    const other = `node:22.14.0-bookworm-slim@sha256:${"2".repeat(64)}`;
    const result = check({
      Dockerfile: [`FROM ${PINNED_NODE} AS build`, `FROM ${other} AS runtime`].join("\n"),
    });

    expect(result.status).toBe(1);
    expect(result.stderr).toContain("pin different digests");
  });

  it("fails two unpinned images that shield each other with the other's stage name", () => {
    const result = check({
      Dockerfile: ["FROM alpine AS busybox", "FROM busybox AS alpine"].join("\n"),
    });

    expect(result.status).toBe(1);
    expect(result.stderr).toContain("FROM alpine is not pinned");
  });

  it("fails a FROM whose own stage name is its image name", () => {
    const result = check({
      Dockerfile: [
        `FROM ${PINNED_NODE} AS build`,
        "FROM busybox AS busybox",
        "COPY --from=busybox /bin/sh /bin/sh",
      ].join("\n"),
    });

    expect(result.status).toBe(1);
    expect(result.stderr).toContain("Dockerfile:2: FROM busybox is not pinned by @sha256 digest");
  });

  it("fails a self-named stage whose image a RUN --mount then mounts", () => {
    const result = check({
      Dockerfile: [
        `FROM ${PINNED_NODE} AS build`,
        "FROM alpine AS alpine",
        "RUN --mount=type=bind,from=alpine,target=/mnt /mnt/bin/true",
      ].join("\n"),
    });

    expect(result.status).toBe(1);
    expect(result.stderr).toContain("Dockerfile:2: FROM alpine is not pinned by @sha256 digest");
  });

  it("holds a self-named node stage to the shared digest", () => {
    const result = check({
      Dockerfile: `FROM ${PINNED_NODE} AS node\nCOPY --from=node /usr/local/bin/node ./\n`,
      "Dockerfile.query": `FROM node:22.14.0-bookworm-slim@${OTHER_DIGEST} AS build\n`,
    });

    expect(result.status).toBe(1);
    expect(result.stderr).toContain("pin different digests");
  });

  it("fails when two Dockerfiles start FROM node images pinned to different digests", () => {
    const result = check({
      Dockerfile: `FROM ${PINNED_NODE} AS build\n`,
      "Dockerfile.query": `FROM node:22.14.0-bookworm-slim@${OTHER_DIGEST} AS build\n`,
    });

    expect(result.status).toBe(1);
    expect(result.stderr).toContain("pin different digests");
  });

  it("passes Cloud Build steps whose builders are pinned by digest", () => {
    const result = check({
      Dockerfile: `FROM ${PINNED_NODE} AS build\n`,
      "cloudbuild.images.yaml": [
        "steps:",
        "  - id: build",
        `    name: gcr.io/cloud-builders/docker@${OTHER_DIGEST}`,
        `  - name: "gcr.io/cloud-builders/docker@${OTHER_DIGEST}" # quoted`,
      ].join("\n"),
    });

    expect([result.status, result.stderr]).toEqual([0, ""]);
    expect(result.stdout).toContain("2 Cloud Build steps in 1 configurations pinned by digest");
  });

  it("fails a Cloud Build step whose builder is a tag", () => {
    const result = check({
      Dockerfile: `FROM ${PINNED_NODE} AS build\n`,
      "cloudbuild.yaml": ["steps:", "  - id: build", "    name: gcr.io/cloud-builders/docker"].join(
        "\n",
      ),
    });

    expect(result.status).toBe(1);
    expect(result.stderr).toContain(
      "cloudbuild.yaml:3: step name gcr.io/cloud-builders/docker is not pinned by @sha256 digest",
    );
  });

  it("fails a Cloud Build step named by a substitution, and one written as a list item", () => {
    const result = check({
      Dockerfile: `FROM ${PINNED_NODE} AS build\n`,
      "cloudbuild.extra.yaml": ["steps:", "  - name: ${_BUILDER}", "  - name: node:22"].join("\n"),
    });

    expect(result.status).toBe(1);
    expect(result.stderr).toContain("cloudbuild.extra.yaml:2: step name ${_BUILDER} is not pinned");
    expect(result.stderr).toContain("cloudbuild.extra.yaml:3: step name node:22 is not pinned");
  });

  it("holds a node builder in Cloud Build to the Dockerfiles' node digest", () => {
    const result = check({
      Dockerfile: `FROM ${PINNED_NODE} AS build\n`,
      "cloudbuild.yaml": `steps:\n  - name: node:22.14.0-bookworm-slim@${OTHER_DIGEST}\n`,
    });

    expect(result.status).toBe(1);
    expect(result.stderr).toContain("pin different digests");
  });

  it("fails an unpinned syntax directive, the frontend image BuildKit runs the file with", () => {
    const result = check({
      Dockerfile: ["# syntax=docker/dockerfile:1", "", `FROM ${PINNED_NODE} AS build`].join("\n"),
    });

    expect(result.status).toBe(1);
    expect(result.stderr).toContain(
      "Dockerfile:1: # syntax docker/dockerfile:1 is not pinned by @sha256 digest",
    );
  });

  it("fails the directive in every spelling BuildKit accepts", () => {
    const result = check({
      Dockerfile: `\uFEFF#SYNTAX = docker/dockerfile:1.7\nFROM ${PINNED_NODE} AS build\n`,
      "Dockerfile.slash": `// syntax=example.com/frontend:latest\nFROM ${PINNED_NODE} AS build\n`,
      "Dockerfile.json": JSON.stringify({ syntax: "example.com/frontend:json" }),
    });

    expect(result.status).toBe(1);
    expect(result.stderr).toContain("Dockerfile:1: # syntax docker/dockerfile:1.7 is not pinned");
    expect(result.stderr).toContain(
      "Dockerfile.slash:1: # syntax example.com/frontend:latest is not pinned",
    );
    expect(result.stderr).toContain(
      "Dockerfile.json:1: # syntax example.com/frontend:json is not pinned",
    );
  });

  it("passes a syntax directive pinned by digest", () => {
    const result = check({
      Dockerfile: [
        `# syntax=docker/dockerfile:1@${OTHER_DIGEST}`,
        "",
        `FROM ${PINNED_NODE} AS build`,
      ].join("\n"),
    });

    expect([result.status, result.stderr]).toEqual([0, ""]);
  });

  it("fails the directive's shape even where BuildKit would read it as a comment", () => {
    // Failing closed: the check never has to model exactly where BuildKit stops reading
    // directives, and a comment that only looks like one is reworded.
    const result = check({
      Dockerfile: [`FROM ${PINNED_NODE} AS build`, "# syntax=docker/dockerfile:1"].join("\n"),
    });

    expect(result.status).toBe(1);
    expect(result.stderr).toContain("Dockerfile:2: # syntax docker/dockerfile:1 is not pinned");
  });

  it("fails when the directory holds no Dockerfile at all", () => {
    const result = check({ "not-a-dockerfile.txt": "FROM node:latest\n" });

    expect(result.status).toBe(1);
    expect(result.stderr).toContain("No Dockerfile* found");
  });
});
