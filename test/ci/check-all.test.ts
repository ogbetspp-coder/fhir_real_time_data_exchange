import { readFileSync } from "node:fs";

import { describe, expect, it } from "vitest";

// scripts/check-all.sh claims to run every gate .github/workflows/ci.yml runs. This holds it to
// that, job by job: the commands each job's section of the script executes must be the commands
// that job runs in CI, in the same order and working directory, but for the few named below.
// Until audit B15 the test asked only whether each CI command appeared somewhere in the script's
// text, which the `step "..."` labels satisfied on their own: every Zone A and Agent command line
// could be deleted with the test still green.

const workflow = readFileSync(".github/workflows/ci.yml", "utf8");
const script = readFileSync("scripts/check-all.sh", "utf8");

// Jobs the script leaves out whole, as its header says: each needs Java or Docker and ~200 MB of
// downloads, and is its own CI job for that reason.
const CI_ONLY_JOBS = new Set(["official-validation", "renderer", "images"]);

// Commands of the compared jobs the script does not run. Installing Node dependencies is the
// caller's `npm ci`. lock-base.sh names the importer lock's base for CI's test (from the pushed
// commit's parent, or origin/main); a local run's test reads the checkout's own origin/main.
const NOT_RUN_LOCALLY = new Set([
  ". npm ci --no-audit --no-fund",
  ". npm ci --no-audit --no-fund --engine-strict",
  ". bash scripts/ci/lock-base.sh",
]);

type Command = string; // "<working directory> <command>", the directory "." for the root

// Every job's executed commands, from the workflow's own layout: a job at two spaces, its
// default working directory under `defaults: run:`, steps at six, a step's keys at eight, and a
// `run: |` block's lines at ten. A layout this does not read fails the test rather than being
// skipped.
export function workflowCommands(text: string): Map<string, Command[]> {
  const body = text.slice(text.indexOf("\njobs:\n") + "\njobs:\n".length);
  const jobs = new Map<string, Command[]>();
  const headers = [...body.matchAll(/^ {2}([a-z][a-z0-9-]*):\n/gm)];
  headers.forEach((header, index) => {
    const job = body.slice(header.index + header[0].length, headers[index + 1]?.index);
    const jobDirectory =
      /^ {4}defaults:\n {6}run:\n(?: {8}.+\n)*? {8}working-directory: (\S+)$/m.exec(job)?.[1] ??
      ".";
    const commands: Command[] = [];
    for (const step of job.split(/^ {6}- /m).slice(1)) {
      const directory = /^ {8}working-directory: (\S+)$/m.exec(step)?.[1] ?? jobDirectory;
      const run = /^(?: {8}|)run:(.*)$/m.exec(step);
      if (run === null) continue;
      const inline = (run[1] ?? "").trim();
      if (/^[>|]/.test(inline) && inline !== "|") throw new Error(`Unread run form: ${inline}`);
      // A block is the lines after `run: |` indented past the step's keys, up to the first that
      // is not.
      const block = /^\n((?: {10}.*\n|\n)*)/.exec(step.slice(run.index + run[0].length))?.[1];
      const lines = inline === "|" ? (block ?? "").split("\n") : [inline];
      for (const line of lines.map((value) => value.trim())) {
        if (line !== "" && !line.startsWith("#")) commands.push(`${directory} ${line}`);
      }
    }
    jobs.set(header[1] ?? "", commands);
  });
  return jobs;
}

// Every section's executed commands: the lines under `# --- <label> (job: <id>) ---` up to the
// next `# --- ` line, without comments, `step` labels and plain assignments. `(cd DIR && ...)`
// runs in DIR, a leading `NAME=value` is the step's environment, and "$UV" is uv.
export function scriptCommands(text: string): Map<string, Command[]> {
  const sections = new Map<string, Command[]>();
  let current: Command[] | undefined;
  for (const raw of text.split("\n")) {
    const line = raw.trim();
    const marker = /^# --- .*?(?:\(job: ([a-z0-9-]+)\))? -+$/.exec(line);
    if (marker !== null) {
      current = marker[1] === undefined ? undefined : [];
      if (marker[1] !== undefined && current !== undefined) sections.set(marker[1], current);
      continue;
    }
    if (current === undefined || line === "" || line.startsWith("#")) continue;
    if (/^step\s/.test(line) || /^[A-Z_][A-Z0-9_]*=("[^"]*"|\S*)$/.test(line)) continue;
    const subshell = /^\(cd (\S+) && (.*)\)$/.exec(line);
    let command = subshell?.[2] ?? line;
    while (/^[A-Z_][A-Z0-9_]*=("[^"]*"|\S*) /.test(command)) {
      command = command.replace(/^[A-Z_][A-Z0-9_]*=("[^"]*"|\S*) /, "");
    }
    current.push(`${subshell?.[1] ?? "."} ${command.replaceAll('"$UV"', "uv")}`);
  }
  return sections;
}

// What differs between the workflow and the script: for each job the script runs, the commands
// only CI runs and those only the script runs (each in order), and any job neither compared nor
// named as left out.
export function drift(ci: string, local: string): Record<string, unknown> {
  const jobs = workflowCommands(ci);
  const sections = scriptCommands(local);
  const found: Record<string, unknown> = {};
  for (const [job, commands] of jobs) {
    if (CI_ONLY_JOBS.has(job)) continue;
    const expected = commands.filter((command) => !NOT_RUN_LOCALLY.has(command));
    const actual = sections.get(job);
    if (actual === undefined) found[job] = "no section of the script runs this job";
    else if (JSON.stringify(actual) !== JSON.stringify(expected)) {
      found[job] = { ci: expected, script: actual };
    }
  }
  for (const job of sections.keys()) {
    if (!jobs.has(job) || CI_ONLY_JOBS.has(job)) found[job] = "no such compared job in CI";
  }
  return found;
}

describe("scripts/check-all.sh", () => {
  it("reads every job of the CI workflow, and the script's three sections", () => {
    const jobs = workflowCommands(workflow);
    expect([...jobs.keys()].sort()).toEqual(
      ["agent", "check", "images", "official-validation", "renderer", "zone-a"].sort(),
    );
    const sections = scriptCommands(script);
    expect([...sections].map(([job, commands]) => [job, commands.length])).toEqual([
      ["check", 2],
      ["zone-a", 8],
      ["agent", 6],
    ]);
    expect(sections.get("zone-a")?.[0]).toBe("zone-a uv sync --frozen");
    for (const command of NOT_RUN_LOCALLY) {
      expect([...jobs.values()].flat()).toContain(command);
    }
  });

  it("runs every command of each compared CI job, in its order and directory", () => {
    expect(drift(workflow, script)).toEqual({});
  });

  it("does not run the steps it documents as left out", () => {
    expect(script).not.toMatch(/^\s*npm ci\b/m);
    expect(script).not.toMatch(/^\s*npm run validate:official\b/m);
    expect(script).not.toMatch(/^\s*npm run renderer:/m);
    expect(script).not.toMatch(/^\s*bash scripts\/ci\/build-images\.sh/m);
  });

  // The seeded failures the old test missed.
  it("fails when a command line is deleted and only its label is left", () => {
    const deleted = script
      .split("\n")
      .filter((line) => !/^\(cd (zone-a|agent) && /.test(line))
      .join("\n");
    expect(deleted).toContain('step "Zone A: uv run --frozen pytest --cov"');
    expect(Object.keys(drift(workflow, deleted)).sort()).toEqual(["agent", "zone-a"]);
  });

  it("fails when a command runs in another directory or out of order", () => {
    const elsewhere = script.replace(
      '(cd zone-a && "$UV" run --frozen mypy --strict)',
      '"$UV" run --frozen mypy --strict',
    );
    expect(Object.keys(drift(workflow, elsewhere))).toEqual(["zone-a"]);
    const swapped = script
      .replace("npm run check\nstep", "npm run build\nstep")
      .replace("npm run build\n\n", "npm run check\n\n");
    expect(swapped).not.toBe(script);
    expect(Object.keys(drift(workflow, swapped))).toEqual(["check"]);
  });

  it("reads a `run: |` block's commands, and fails on a CI job the script does not know", () => {
    const block = workflow.replace(
      "        run: npm run build\n",
      "        run: |\n          npm run build\n          npm run extra\n",
    );
    expect(workflowCommands(block).get("check")).toContain(". npm run extra");
    expect(drift(block, script)).toEqual({
      check: {
        ci: [". npm run check", ". npm run build", ". npm run extra"],
        script: [". npm run check", ". npm run build"],
      },
    });
    const added = `${workflow}\n  lint:\n    name: Lint\n    steps:\n      - name: Lint\n        run: npm run lint\n`;
    expect(drift(added, script)).toEqual({ lint: "no section of the script runs this job" });
  });
});
