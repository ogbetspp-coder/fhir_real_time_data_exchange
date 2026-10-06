import { defineConfig } from "vitest/config";

// The tests that need the pinned HL7 validator, Java 21 and the pinned packages
// (.cache/official-validator, which `npm run validate:official` fetches, checks and seeds): the
// EMA profile's section slots against the mapping, and the StructureMap twin against the
// crosswalk. CI's Official validation job runs them after the validator (`npm run test:official`);
// the default suite (vitest.config.ts) leaves them out, since `npm run check` has no Java.
export default defineConfig({
  test: {
    include: ["test/official/**/*.test.ts"],
    testTimeout: 900_000,
    hookTimeout: 900_000,
    fileParallelism: false,
  },
});
