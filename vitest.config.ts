import { configDefaults, defineConfig } from "vitest/config";

// The TypeScript suite is everything under test/. Excluded on top of vitest's defaults: agent
// worktrees that Claude Code checks out under .claude/ (each a full copy of this repository,
// which would run the suite once per worktree), the two Python deployables and the label
// reader, which carry their own gates.
//
// Coverage runs with `npm run test:coverage` (what `npm run check`, and so CI's Check job and
// the deploy's quality gate, run), not with a bare `vitest`. The floors are ratchets: each is
// the measured figure at the commit that set it, rounded down to a whole percent, so a change
// that deletes tests or adds untested code under a directory fails the gate. Raise a floor when
// a directory's coverage rises; lowering one is a decision to write down in the commit.
export default defineConfig({
  test: {
    exclude: [
      ...configDefaults.exclude,
      ".claude/**",
      "agent/**",
      "zone-a/**",
      "label-docx-reader/**",
    ],
    coverage: {
      provider: "v8",
      include: ["src/**"],
      // The renderer image's fontconfig is XML, not code.
      exclude: ["src/render/image/**"],
      // Summaries only: the text one for the log, the JSON one for anyone comparing runs. The
      // directory is git-, prettier-, eslint- and docker-ignored.
      reporter: ["text-summary", "json-summary"],
      reportsDirectory: "coverage",
      // Raised to the measured figures on 2026-09-28 (audit B15): until then most floors had never
      // moved from the commit that set them and sat 2 to 10 points below the measurement, so a
      // change could drop that much coverage and pass. Every directory of src/ has one
      // (test/ci/coverage.test.ts).
      thresholds: {
        // All of src/ together, which also holds the entry points (server.ts, config.ts).
        lines: 97,
        statements: 96,
        functions: 98,
        branches: 90,
        "src/fidelity/**": { lines: 99, statements: 98, functions: 100, branches: 92 },
        "src/contracts/**": { lines: 97, statements: 96, functions: 97, branches: 92 },
        "src/query/**": { lines: 96, statements: 93, functions: 97, branches: 86 },
        "src/fhir/**": { lines: 99, statements: 98, functions: 100, branches: 92 },
        "src/gcp/**": { lines: 94, statements: 92, functions: 94, branches: 88 },
        // The run's entry points, each on its own: a gate the pipeline stops at, or a failure
        // the API classifies, that no test reaches lowers these (test/persisted-run.test.ts).
        "src/pipeline.ts": { lines: 97, statements: 97, functions: 100, branches: 81 },
        "src/app.ts": { lines: 100, statements: 97, functions: 100, branches: 87 },
        "src/lib/**": { lines: 94, statements: 94, functions: 100, branches: 91 },
        "src/authority/**": { lines: 98, statements: 97, functions: 99, branches: 92 },
        "src/render/**": { lines: 97, statements: 96, functions: 99, branches: 87 },
        "src/fixtures/**": { lines: 94, statements: 94, functions: 100, branches: 80 },
      },
    },
  },
});
