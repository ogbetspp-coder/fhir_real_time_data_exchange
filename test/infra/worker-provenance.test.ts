import { describe, expect, it } from "vitest";

import { readInfra, terraformBlocks } from "../support/terraform.js";

// A signed run manifest records runtime.sourceCommit and runtime.imageDigest from GIT_COMMIT and
// IMAGE_DIGEST (src/pipeline.ts). Until 2026-09-22 Terraform set neither on the worker, so every
// manifest the deployed worker signed said "development" for both and could not be tied to the
// code or the image that produced it. These assertions keep the two variables on the worker and
// sourced from the deploy's own inputs.

function workerContainer(): string {
  const worker = terraformBlocks(readInfra()).find(
    ({ type, name }) => type === "google_cloud_run_v2_service" && name === "worker",
  );
  if (worker === undefined) throw new Error("google_cloud_run_v2_service.worker not found");
  const at = worker.body.indexOf('name       = "worker"');
  expect(at).toBeGreaterThan(-1);
  return worker.body.slice(at);
}

function envValue(container: string, name: string): string | undefined {
  return new RegExp(`env \\{\\s*name\\s*=\\s*"${name}"\\s*value\\s*=\\s*([^\\n]+)\\n`).exec(
    container,
  )?.[1];
}

describe("the worker's provenance environment", () => {
  it("sets GIT_COMMIT from the deployed commit (service_version)", () => {
    expect(envValue(workerContainer(), "GIT_COMMIT")?.trim()).toBe("var.service_version");
  });

  it("sets IMAGE_DIGEST from the digest of the worker image reference", () => {
    expect(envValue(workerContainer(), "IMAGE_DIGEST")?.trim()).toBe("local.worker_image_digest");
    const infra = readInfra();
    expect(infra).toContain(
      'worker_image_digest = try(regex("@(sha256:[0-9a-f]{64})$", var.worker_image)[0], null)',
    );
  });

  it("refuses a worker image without a digest at plan time", () => {
    const worker = terraformBlocks(readInfra()).find(
      ({ type, name }) => type === "google_cloud_run_v2_service" && name === "worker",
    );
    expect(worker?.body).toMatch(
      /precondition \{\s*condition\s*=\s*local\.worker_image_digest != null/,
    );
  });

  // Run manifest 4.0.0 names the validator sidecar that checked the run (audit B07, S-4).
  it("sets VALIDATOR_IMAGE_DIGEST from the validator image reference, which must carry one", () => {
    expect(envValue(workerContainer(), "VALIDATOR_IMAGE_DIGEST")?.trim()).toBe(
      "local.validator_image_digest",
    );
    const infra = readInfra();
    expect(infra).toMatch(
      /validator_image_digest = try\(regex\("@\(sha256:\[0-9a-f\]\{64\}\)\$", var\.validator_image\)\[0\], null\)/,
    );
    const worker = terraformBlocks(infra).find(
      ({ type, name }) => type === "google_cloud_run_v2_service" && name === "worker",
    );
    expect(worker?.body).toMatch(
      /precondition \{\s*condition\s*=\s*local\.validator_image_digest != null/,
    );
  });

  // The packages come from the lock in the image; a free-form variable named one until 4.0.0.
  it("no longer names a standard package in the environment", () => {
    expect(envValue(workerContainer(), "GLOBAL_EPI_PACKAGE")).toBeUndefined();
    expect(readInfra()).not.toMatch(/name\s*=\s*"GLOBAL_EPI_PACKAGE"/);
  });
});
