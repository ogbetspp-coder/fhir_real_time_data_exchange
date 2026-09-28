import { readFileSync } from "node:fs";

import { describe, expect, it } from "vitest";

// The deploy's profile import (docs/foundations.md, E2). Skipping it saves about 15 minutes a
// deploy, and skipping it wrongly leaves the validated store without the profiles it validates
// against. So a skip needs the recorded fingerprint of the set, and the store's own fingerprint
// (every resource of the import's types, with its version), to agree; until audit B08 the store
// was judged by its StructureDefinition count alone. test/infra/bootstrap.test.ts runs it.

const bootstrap = readFileSync("scripts/gcp/bootstrap.sh", "utf8");

describe("the profile import", () => {
  it("is skipped only when the set and the store are both as recorded, and nothing is missing", () => {
    expect(bootstrap).toContain(
      'if [[ "${FORCE_PROFILE_IMPORT:-false}" != "true" && "$recorded_set" == "$FINGERPRINT" &&\n  "$recorded_store" == "$in_store" && "$missing" == "0" ]]; then',
    );
  });

  it("fingerprints content, path, dataset and store", () => {
    expect(bootstrap).toContain('f"dataset={dataset}\\nstore={store}\\n"');
    expect(bootstrap).toContain("os.path.relpath(path, root)");
    expect(bootstrap).toContain('hashlib.sha256(open(path, "rb").read())');
  });

  it("records the fingerprint only after every import succeeded", () => {
    const loopEnd = bootstrap.indexOf("  done\n", bootstrap.indexOf("for prefix in terminology"));
    const record = bootstrap.indexOf('gcloud --quiet storage cp - "$MARKER"');
    expect(loopEnd).toBeGreaterThan(0);
    expect(record).toBeGreaterThan(loopEnd);
  });
});
