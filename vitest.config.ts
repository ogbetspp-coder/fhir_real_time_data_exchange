import { configDefaults, defineConfig } from "vitest/config";

// The TypeScript suite is everything under test/ (and the spike tests beside it). Excluded on
// top of vitest's defaults: agent worktrees that Claude Code checks out under .claude/ (each a
// full copy of this repository, which would run the suite once per worktree), and the two
// Python deployables, which carry their own gates.
export default defineConfig({
  test: {
    exclude: [...configDefaults.exclude, ".claude/**", "agent/**", "zone-a/**"],
  },
});
