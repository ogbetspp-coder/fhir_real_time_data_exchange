import { readFileSync } from "node:fs";

import { describe, expect, it } from "vitest";

// The deploy's profile import (docs/foundations.md, E2). Skipping it saves about 15 minutes a
// deploy, and skipping it wrongly leaves the validated store without the profiles it validates
// against. So a skip needs both the recorded fingerprint and the store's own count to agree.

const bootstrap = readFileSync("scripts/gcp/bootstrap.sh", "utf8");

describe("the profile import", () => {
  it("is skipped only when the fingerprint matches and the store holds every profile", () => {
    expect(bootstrap).toContain(
      'if [[ "${FORCE_PROFILE_IMPORT:-false}" != "true" && "$recorded" == "$FINGERPRINT" && "$in_store" == "$EXPECTED_PROFILES" ]]; then',
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
