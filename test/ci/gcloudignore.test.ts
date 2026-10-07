import { execFileSync } from "node:child_process";
import { readFileSync } from "node:fs";
import path from "node:path";

import ignoreModule, { type Ignore } from "ignore";
import { describe, expect, it } from "vitest";

import { copySources, instructions } from "../../scripts/ci/dockerfile.mjs";

// What `gcloud builds submit .` uploads (scripts/gcp/deploy.sh, phase_images) is the source Cloud
// Build builds every image from and runs every step on. A path .gcloudignore leaves out is missing
// there, though CI's Images job, which builds from the full checkout, never notices: the label
// reader was left out while the worker image copied it (review of #196). This holds every path
// the images are built from, every path dependency their copied lock files name, and every path a
// build step reads, to surviving .gcloudignore, read with gitignore's semantics (the `ignore`
// library, as gcloud reads the file) and gcloud's `#!include:` directive.

// A CommonJS package whose types say `export default`: under NodeNext the default import is its
// module.exports, which also carries itself as `default`.
const ignore = ignoreModule.default;

// The Dockerfiles Cloud Build builds (test/ci/images.test.ts holds cloudbuild.images.yaml to them).
const BUILT = ["Dockerfile", "Dockerfile.validator"];

export function gcloudIgnore(text: string, read = (file: string) => readFileSync(file, "utf8")) {
  const lines = text.split(/\r?\n/).flatMap((line) => {
    const include = /^#!include:(.+)$/.exec(line);
    return include === null ? [line] : read((include[1] ?? "").trim()).split(/\r?\n/);
  });
  return ignore().add(lines);
}

function tracked(source: string): string[] {
  return execFileSync("git", ["ls-files", "-z", "--", source], { encoding: "utf8" })
    .split("\0")
    .filter((file) => file.length > 0);
}

// Each source's tracked files that the upload leaves out; a source git does not track is reported
// whole, so a path written wrong fails too.
function leftOut(sources: string[], uploaded: Ignore): string[] {
  return sources.flatMap((source) => {
    const files = tracked(source);
    return files.length === 0
      ? [`${source} (not tracked)`]
      : files.filter((f) => uploaded.ignores(f));
  });
}

const copied = BUILT.flatMap((file) =>
  copySources(instructions(readFileSync(file, "utf8"))).map((source) => path.normalize(source)),
);

// The projects a copied uv.lock names by path (`editable`, `directory` or `path`), each by the
// pyproject.toml uv builds it from.
const pathDependencies = copied
  .filter((source) => path.basename(source) === "uv.lock")
  .flatMap((lock) =>
    [
      ...readFileSync(lock, "utf8").matchAll(
        /^source = \{ (?:editable|directory|path) = "([^"]+)" \}$/gm,
      ),
    ].map(([, at]) => path.join(path.dirname(lock), at ?? "", "pyproject.toml")),
  );

// The repository paths Cloud Build's steps read, as its configuration and the scripts it runs name
// them.
const stepInputs = [
  ...new Set(
    ["cloudbuild.images.yaml", "scripts/ci/worker-recompute-smoke.sh"].flatMap((file) =>
      [...readFileSync(file, "utf8").matchAll(/\b(?:scripts|test)\/[\w./-]*[\w-]/g)].map(
        ([found]) => found,
      ),
    ),
  ),
];

describe(".gcloudignore", () => {
  const uploaded = gcloudIgnore(readFileSync(".gcloudignore", "utf8"));

  it("uploads every path the images are built from", () => {
    expect(copied).toContain("label-docx-reader/src");
    expect(leftOut(copied, uploaded)).toEqual([]);
  });

  it("uploads every project a copied lock file names by path, and the image copies it", () => {
    expect(pathDependencies).toEqual(["label-docx-reader/pyproject.toml", "zone-a/pyproject.toml"]);
    for (const project of pathDependencies) {
      expect([project, copied.includes(project)]).toEqual([project, true]);
    }
    expect(leftOut(pathDependencies, uploaded)).toEqual([]);
  });

  it("uploads every path a build step reads", () => {
    expect(stepInputs).toEqual(
      expect.arrayContaining([
        "scripts/ci/worker-recompute-smoke.sh",
        "scripts/ci/worker-recompute-smoke.mjs",
        "test/fixtures/certified-word/recompute",
        "scripts/ci/validator-offline.mjs",
      ]),
    );
    expect(leftOut(stepInputs, uploaded)).toEqual([]);
  });

  it("is read as gcloud reads it, so the label reader's old exclusion is caught", () => {
    const old = gcloudIgnore(".gcloudignore\n#!include:.gitignore\nlabel-docx-reader/\n");
    expect(leftOut(["label-docx-reader/src"], old).length).toBeGreaterThan(0);
    // The included .gitignore applies, and its negation too.
    expect(old.ignores("node_modules/x/index.js")).toBe(true);
    expect(old.ignores(".env.example")).toBe(false);
    expect(old.ignores("zone-a/src/zone_a/__pycache__/recompute.cpython-314.pyc")).toBe(true);
    // What is still left out is what no image or step reads.
    expect(uploaded.ignores("label-docx-reader/corpus/README.md")).toBe(true);
    expect(uploaded.ignores("label-docx-reader/tests/test_reader.py")).toBe(true);
  });
});
