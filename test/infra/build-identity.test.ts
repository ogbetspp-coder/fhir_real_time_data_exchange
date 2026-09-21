import { readFileSync } from "node:fs";

import { describe, expect, it } from "vitest";

import { readInfra, serviceAccountRoles, terraformBlocks } from "../support/terraform.js";

// Image builds run in the EU under an identity that can only build (docs/foundations.md A2, B3).
// Both halves are easy to lose without anyone noticing: drop one flag from `gcloud builds submit`
// and gcloud quietly falls back to a global build, as the default compute service account,
// staging the source in a US bucket. These tests are what notices.

describe("the image build identity", () => {
  const terraform = readInfra();

  it("holds exactly three roles, each on the narrowest resource that serves", () => {
    const roles = serviceAccountRoles(terraform, "build");
    expect(roles).toEqual(
      expect.arrayContaining([
        // Push to this product's repository — bound on the repository, not the project.
        {
          type: "google_artifact_registry_repository_iam_member",
          role: "roles/artifactregistry.writer",
        },
        // Read the uploaded source — bound on the staging bucket, not the project.
        { type: "google_storage_bucket_iam_member", role: "roles/storage.objectViewer" },
        // Write its own build log.
        { type: "google_project_iam_member", role: "roles/logging.logWriter" },
      ]),
    );
    expect(roles).toHaveLength(3);
  });

  it("stages source in the deployment region, never in a fixed or multi-region location", () => {
    const bucket = terraformBlocks(terraform).find(
      ({ type, name }) => type === "google_storage_bucket" && name === "build_staging",
    );
    expect(bucket).toBeDefined();
    expect(bucket?.body).toMatch(/\blocation\s*=\s*var\.region\b/);
    expect(bucket?.body).toMatch(/public_access_prevention\s*=\s*"enforced"/);
  });

  it("gives the default compute service account nothing", () => {
    // It used to hold roles/cloudbuild.builds.builder so that builds could run as it. A grant
    // to it anywhere in infra/ means a build, or anything else, is running as the identity
    // every Compute Engine default uses.
    expect(terraform).not.toMatch(/-compute@developer\.gserviceaccount\.com/);
  });
});

describe("the deploy's image build", () => {
  const script = readFileSync("scripts/gcp/deploy.sh", "utf8");
  const submit = /gcloud --quiet builds submit[^;]*?\.;\s*then/s.exec(script)?.[0] ?? "";

  it("is the one build submission, and it was found", () => {
    expect(submit).not.toBe("");
    expect(script.match(/builds submit/g)).toHaveLength(1);
  });

  it("names the region, the build identity and the EU staging bucket explicitly", () => {
    expect(submit).toMatch(/--region="\$REGION"/);
    expect(submit).toMatch(
      /--service-account="projects\/\$\{PROJECT_ID\}\/serviceAccounts\/\$\{build_account\}"/,
    );
    expect(submit).toMatch(/--gcs-source-staging-dir="\$staging_dir"/);
    expect(script).toMatch(/build_account="ema-flow-build-\$\{ENVIRONMENT\}@/);
    expect(script).toMatch(
      /staging_dir="gs:\/\/\$\{PROJECT_ID\}-ema-flow-\$\{ENVIRONMENT\}-build-staging\//,
    );
  });

  it("creates the build identity before it builds", () => {
    // phase_apis runs a targeted apply before phase_images; the build fails if the account, the
    // bucket or a grant is missing from that list.
    for (const address of [
      "google_service_account.build",
      "google_storage_bucket.build_staging",
      "google_storage_bucket_iam_member.build_staging_reader",
      "google_artifact_registry_repository_iam_member.build_writer",
      "google_project_iam_member.build_log_writer",
    ]) {
      expect(script).toContain(`-target=${address}`);
    }
    // And never the deployer's actAs grant: that apply passes no deployer_account, so targeting
    // it there would destroy it.
    expect(script).not.toContain(
      "-target=google_service_account_iam_member.deployer_acts_as_build",
    );
  });

  it("grants the build identity nothing imperatively", () => {
    // The Terraform reader cannot see a grant made by a shell script. No script may make one.
    for (const file of [
      "scripts/gcp/deploy.sh",
      "scripts/gcp/bootstrap.sh",
      "scripts/gcp/common.sh",
    ]) {
      const text = readFileSync(file, "utf8");
      expect([file, /add-iam-policy-binding[^\n]*ema-flow-build/.test(text)]).toEqual([
        file,
        false,
      ]);
    }
  });

  it("logs to Cloud Logging only, which a user-specified service account requires", () => {
    expect(readFileSync("cloudbuild.images.yaml", "utf8")).toMatch(/logging:\s*CLOUD_LOGGING_ONLY/);
  });
});
