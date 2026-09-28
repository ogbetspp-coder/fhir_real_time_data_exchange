import { configDefaults, defineConfig } from "vitest/config";

// The TypeScript suite is everything under test/ (and the spike tests beside it). Excluded on
// top of vitest's defaults: agent worktrees that Claude Code checks out under .claude/ (each a
// full copy of this repository, which would run the suite once per worktree), and the two
// Python deployables, which carry their own gates.
//
// Coverage runs with `npm run test:coverage` (what `npm run check`, and so CI's Check job and
// the deploy's quality gate, run), not with a bare `vitest`. The floors are ratchets: each is
// the measured figure at the commit that set it, rounded down to a whole percent, so a change
// that deletes tests or adds untested code under a directory fails the gate. Raise a floor when
// a directory's coverage rises; lowering one is a decision to write down in the commit.
export default defineConfig({
  test: {
    exclude: [...configDefaults.exclude, ".claude/**", "agent/**", "zone-a/**"],
    coverage: {
      provider: "v8",
      include: ["src/**"],
      // The renderer image's fontconfig is XML, not code.
      exclude: ["src/render/image/**"],
      // Summaries only: the text one for the log, the JSON one for anyone comparing runs. The
      // directory is git-, prettier-, eslint- and docker-ignored.
      reporter: ["text-summary", "json-summary"],
      reportsDirectory: "coverage",
      thresholds: {
        // All of src/ together, which also holds the entry points and fixtures below.
        lines: 92,
        statements: 90,
        functions: 92,
        branches: 82,
        "src/fidelity/**": { lines: 98, statements: 97, functions: 100, branches: 91 },
        "src/contracts/**": { lines: 94, statements: 93, functions: 95, branches: 82 },
        "src/query/**": { lines: 94, statements: 89, functions: 94, branches: 81 },
        "src/fhir/**": { lines: 99, statements: 98, functions: 100, branches: 92 },
        "src/gcp/**": { lines: 93, statements: 90, functions: 92, branches: 85 },
        // The run's entry points, each on its own: a gate the pipeline stops at, or a failure
        // the API classifies, that no test reaches lowers these (test/persisted-run.test.ts).
        "src/pipeline.ts": { lines: 97, statements: 95, functions: 100, branches: 80 },
        "src/app.ts": { lines: 100, statements: 97, functions: 100, branches: 87 },
        "src/lib/**": { lines: 90, statements: 90, functions: 100, branches: 90 },
        "src/authority/**": { lines: 93, statements: 91, functions: 92, branches: 85 },
        "src/render/**": { lines: 97, statements: 96, functions: 96, branches: 86 },
      },
    },
  },
});
