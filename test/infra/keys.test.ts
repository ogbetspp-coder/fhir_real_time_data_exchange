import { readFileSync } from "node:fs";

import { describe, expect, it } from "vitest";

import { readInfra, terraformBlocks } from "../support/terraform.js";

// The customer-managed keys for the record (docs/design/cmek-rollout.md, step 1). Losing a key
// loses the data it encrypts — a Healthcare dataset whose key is unavailable is disabled in an hour
// and deleted in 30 days — so the properties that stop a key being lost are pinned here.

const keys = readFileSync("infra/keys.tf", "utf8");
const blocks = terraformBlocks(readInfra());
const block = (type: string, name: string) =>
  blocks.find((candidate) => candidate.type === type && candidate.name === name)?.body ?? "";

describe("the record keys", () => {
  const map = /record_keys = \{([\s\S]*?)\n {2}\}\n\}/.exec(keys)?.[1] ?? "";
  const names = [...map.matchAll(/^\s{4}([a-z-]+) = \{/gm)].map((match) => match[1]);
  const agents = [...map.matchAll(/agent\s*=\s*(\S+)/g)].map((match) => match[1]);

  it("are one per purpose", () => {
    expect(names).toEqual([
      "fhir-record",
      "ledger-analytics",
      "audit-logs",
      "artifacts",
      "platform-storage",
    ]);
  });

  it("are each granted to a different service agent, and only to it", () => {
    expect(agents).toHaveLength(names.length);
    expect(new Set(agents).size).toBe(agents.length);
    const grant = block("google_kms_crypto_key_iam_member", "record_agent");
    expect(grant).toMatch(/for_each\s*=\s*local\.record_keys/);
    expect(grant).toMatch(/role\s*=\s*"roles\/cloudkms\.cryptoKeyEncrypterDecrypter"/);
    expect(grant).toMatch(/member\s*=\s*"serviceAccount:\$\{each\.value\.agent\}"/);
  });

  it("cannot be destroyed by an apply, and wait the longest allowed before destruction", () => {
    expect(keys).toMatch(/key_destroy_wait = "10368000s"/);
    for (const name of ["record", "manifest_signing_hsm"]) {
      const body = block("google_kms_crypto_key", name);
      expect(body).toMatch(/destroy_scheduled_duration\s*=\s*local\.key_destroy_wait/);
      expect(body).toMatch(/lifecycle\s*\{[^}]*prevent_destroy\s*=\s*true/);
    }
  });

  it("rotate, which never re-encrypts, so no version is ever destroyed", () => {
    expect(block("google_kms_crypto_key", "record")).toMatch(/rotation_period\s*=\s*"7776000s"/);
  });
});

describe("the manifest signing key", () => {
  it("is held in an HSM, with the algorithm the software key used, so verification is unchanged", () => {
    const hsm = block("google_kms_crypto_key", "manifest_signing_hsm");
    const software = block("google_kms_crypto_key", "manifest_signing");
    expect(hsm).toMatch(/protection_level\s*=\s*"HSM"/);
    const algorithm = (text: string) => /algorithm\s*=\s*"([^"]+)"/.exec(text)?.[1];
    expect(algorithm(hsm)).toBe("RSA_SIGN_PSS_2048_SHA256");
    expect(algorithm(hsm)).toBe(algorithm(software));
  });
});

describe("the key availability alert", () => {
  const alert = block("google_monitoring_alert_policy", "key_availability");

  it("fires on destruction, on a version leaving ENABLED, and on any grant change", () => {
    expect(alert).toContain('protoPayload.methodName=\\"DestroyCryptoKeyVersion\\"');
    expect(alert).toContain(
      'protoPayload.methodName=\\"UpdateCryptoKeyVersion\\" AND NOT protoPayload.request.cryptoKeyVersion.state=\\"ENABLED\\"',
    );
    expect(alert).toContain('protoPayload.methodName=\\"SetIamPolicy\\"');
    expect(alert).toContain("cloudaudit.googleapis.com%2Factivity");
  });
});

describe("the key deny policy", () => {
  const guard = readFileSync("scripts/gcp/key-guard.sh", "utf8");

  it("denies everyone destroying or disabling a key version", () => {
    const first =
      /"Nobody may destroy or disable a key version\."[\s\S]*?\]\s*\}/.exec(guard)?.[0] ?? "";
    expect(first).toContain('"deniedPrincipals": ["principalSet://goog/public:all"]');
    expect(first).toContain("cloudkms.googleapis.com/cryptoKeyVersions.destroy");
    expect(first).toContain("cloudkms.googleapis.com/cryptoKeyVersions.update");
    expect(first).not.toContain("exceptionPrincipals");
  });

  it("lets only the deployer change key grants", () => {
    const second = /"Only the deployer[\s\S]*?\]\s*\}/.exec(guard)?.[0] ?? "";
    expect(second).toContain("cloudkms.googleapis.com/cryptoKeys.setIamPolicy");
    expect(second).toContain("cloudkms.googleapis.com/keyRings.setIamPolicy");
    expect(second).toMatch(
      /"exceptionPrincipals": \["principal:\/\/iam\.googleapis\.com\/projects\/-\/serviceAccounts\/\$\{DEPLOYER\}"\]/,
    );
  });
});

describe("who signs run manifests", () => {
  it("the worker signs with the HSM key's named version, and holds nothing on the software key", () => {
    const run = readFileSync("infra/run.tf", "utf8");
    expect(run).toContain(
      'value = "${google_kms_crypto_key.manifest_signing_hsm.id}/cryptoKeyVersions/${var.kms_manifest_key_version}"',
    );
    const grantsOnSoftwareKey = blocks.filter(
      ({ type, body }) =>
        type === "google_kms_crypto_key_iam_member" &&
        /crypto_key_id\s*=\s*google_kms_crypto_key\.manifest_signing\.id/.test(body),
    );
    expect(grantsOnSoftwareKey).toEqual([]);
  });
});
