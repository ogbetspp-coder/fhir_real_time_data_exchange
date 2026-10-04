import { execFileSync } from "node:child_process";
import {
  mkdirSync,
  mkdtempSync,
  readdirSync,
  readFileSync,
  rmSync,
  statSync,
  writeFileSync,
} from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";

import { describe, expect, it } from "vitest";

import { DIFF_ARGS, NOT_INPUTS, rendererInputsChanged } from "../../scripts/ci/renderer-inputs.mjs";

const SCRIPT = path.resolve("scripts/ci/renderer-inputs.mjs");

// The step as CI runs it, in a scratch repository whose HEAD is a pull request's merge commit:
// `change` is made on the branch; what the step prints, and what it writes to the job's summary.
// The event's base and head are the merge commit's parents unless `event` says otherwise.
type EventShas = (parents: { base: string; head: string }) => Record<string, string>;
const TRUE_EVENT: EventShas = ({ base, head }) => ({ BASE_SHA: base, HEAD_SHA: head });

function mergeRun(
  change: (git: (...args: string[]) => void, root: string) => void,
  event: EventShas = TRUE_EVENT,
): {
  output: string;
  summary: string;
} {
  const root = mkdtempSync(path.join(tmpdir(), "renderer-inputs-"));
  try {
    // A clean environment: no GIT_DIR or the like from whatever runs the tests.
    const env = Object.fromEntries(
      Object.entries(process.env).filter(([key]) => !key.startsWith("GIT_")),
    );
    const git = (...args: string[]): void => {
      execFileSync(
        "git",
        [
          "-c",
          "user.name=t",
          "-c",
          "user.email=t@example.org",
          "-c",
          "commit.gpgsign=false",
          ...args,
        ],
        { cwd: root, env, stdio: "ignore" },
      );
    };
    git("init", "-q", "-b", "main");
    mkdirSync(path.join(root, "scripts/render"), { recursive: true });
    mkdirSync(path.join(root, "docs"));
    writeFileSync(path.join(root, "scripts/render/check-drawings.ts"), "export const x = 1;\n");
    writeFileSync(path.join(root, "docs/note.md"), "a note\n");
    git("add", "-A");
    git("commit", "-q", "-m", "base");
    git("checkout", "-q", "-b", "change");
    change(git, root);
    git("add", "-A");
    git("commit", "-q", "-m", "change");
    git("checkout", "-q", "main");
    git("merge", "-q", "--no-ff", "-m", "merge", "change");
    const summary = path.join(root, "summary.md");
    writeFileSync(summary, "");
    const parent = (ref: string): string =>
      execFileSync("git", ["rev-parse", ref], { cwd: root, env, encoding: "utf8" }).trim();
    const shas = event({ base: parent("HEAD^1"), head: parent("HEAD^2") });
    const output = execFileSync(process.execPath, [SCRIPT], {
      cwd: root,
      env: { ...env, EVENT_NAME: "pull_request", GITHUB_STEP_SUMMARY: summary, ...shas },
      encoding: "utf8",
      stdio: ["ignore", "pipe", "ignore"],
    });
    return { output, summary: readFileSync(summary, "utf8") };
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
}
import { MOUNTS } from "../../scripts/render/run.mjs";

// Which pull requests skip CI's Renderer job's checks (scripts/ci/renderer-inputs.mjs). The
// failure this guards against is the quiet one: a path a renderer check reads classed as not an
// input, so a change to it merges with the checks skipped. Every file the image's run mounts, and
// every file the image and the job are built from, must stay an input.

function files(at: string): string[] {
  if (!statSync(at).isDirectory()) return [at];
  return readdirSync(at).flatMap((entry) => files(path.join(at, entry)));
}

const workflow = readFileSync(".github/workflows/ci.yml", "utf8");

describe("the renderer's inputs", () => {
  it("are every file the image's run mounts, and every file the image and the job are built from", () => {
    const read = MOUNTS.filter((mounted) => mounted !== "node_modules").flatMap(files);
    expect(read.length).toBeGreaterThan(100);
    for (const file of [
      ...read,
      "Dockerfile.renderer",
      "src/render/image/fonts.conf",
      "package-lock.json",
      ".dockerignore",
      ".github/workflows/ci.yml",
      "scripts/ci/renderer-inputs.mjs",
      "scripts/render/run.mjs",
      "test/render/new.test.ts",
      "anything-new.json",
    ]) {
      // Markdown is never read by a check (a label's README is the lock's, not the scripts').
      if (file.endsWith(".md")) continue;
      expect([file, rendererInputsChanged([file])]).toEqual([file, true]);
    }
  });

  it("skip only documentation, the Python deployables, the label reader, the infrastructure, assistant settings and Markdown", () => {
    expect(NOT_INPUTS).toHaveLength(8);
    for (const file of [
      "docs/design/authority-import-renderer.md",
      "README.md",
      "labels/ema-epi/README.md",
      "agent/src/a.py",
      "zone-a/tests/test_x.py",
      "label-docx-reader/tests/test_x.py",
      "infra/main.tf",
      ".claude/settings.json",
      ".cursor/rules/x.mdc",
    ]) {
      expect([file, rendererInputsChanged([file])]).toEqual([file, false]);
    }
    expect(rendererInputsChanged(["docs/x.md", "src/render/page.ts"])).toBe(true);
  });

  it("run every check when nothing is known to have changed", () => {
    expect(rendererInputsChanged([])).toBe(true);
  });

  it("are read from a merge commit with renames undetected, so an input moved into docs/ still runs every check", () => {
    expect(DIFF_ARGS).toContain("--no-renames");
    for (const target of ["docs/check-drawings.ts", "docs/check-drawings.md"]) {
      const moved = mergeRun((git) => git("mv", "scripts/render/check-drawings.ts", target));
      expect([target, moved.output]).toEqual([target, "run=true\n"]);
      expect(moved.summary).toBe("");
    }
  });

  it("skip a merge commit that changes only documentation, and say so on the job's summary", () => {
    const docs = mergeRun((_, root) => writeFileSync(path.join(root, "docs/note.md"), "changed\n"));
    expect(docs.output).toBe("run=false\n");
    expect(docs.summary).toMatch(/^Renderer checks skipped: no renderer input changed \(1 files/u);
  });

  // A merge commit other than the one the event describes (GitHub recomputed it after the event,
  // or something else was checked out) is not trusted to skip anything.
  it.each<[string, EventShas]>([
    [
      "a base that is not the merge commit's first parent",
      ({ head }) => ({ BASE_SHA: head, HEAD_SHA: head }),
    ],
    ["a head that is not its second parent", ({ base }) => ({ BASE_SHA: base, HEAD_SHA: base })],
    ["no base or head named at all", () => ({})],
  ])("run every check for %s", (_, event) => {
    const docs = mergeRun(
      (_git, root) => writeFileSync(path.join(root, "docs/note.md"), "changed\n"),
      event,
    );
    expect(docs.output).toBe("run=true\n");
    expect(docs.summary).toBe("");
  });

  it("are told the event's base and head in CI", () => {
    const step =
      /- name: Name whether a renderer input changed\n([\s\S]*?)run: /u.exec(workflow)?.[1] ?? "";
    expect(step).toContain("BASE_SHA: ${{ github.event.pull_request.base.sha }}");
    expect(step).toContain("HEAD_SHA: ${{ github.event.pull_request.head.sha }}");
  });

  it("gate every step of the Renderer job after the one that decides", () => {
    const job = /^ {2}renderer:\n([\s\S]*?)(?=^ {2}[a-z-]+:\n)/mu.exec(workflow)?.[1] ?? "";
    const steps = job.split(/^ {6}- /mu).slice(1);
    const decides = steps.findIndex((step) => step.includes("id: inputs"));
    expect(steps[decides]).toContain("node scripts/ci/renderer-inputs.mjs");
    expect(steps.length - decides).toBeGreaterThan(7);
    for (const step of steps.slice(decides + 1)) {
      expect(step).toContain("if: steps.inputs.outputs.run == 'true'");
    }
  });
});
