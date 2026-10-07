import { createHash } from "node:crypto";

import { OAuth2Client } from "google-auth-library";
import { beforeAll, describe, expect, it } from "vitest";

import { loadEmaMapping, type EmaMapping } from "../../src/fhir/mapping.js";
import { crc32c } from "../../src/lib/crc32c.js";
import { loadSignerConfig } from "../../src/signer/config.js";
import { buildSignerApp, kmsSigner } from "../../src/signer/wiring.js";

// The signer's configuration and its Cloud KMS signing, as the entry point builds them.

const KEY_VERSION =
  "projects/p/locations/europe-west4/keyRings/ema-flow-dev-evidence/cryptoKeys/approval-signing-hsm/cryptoKeyVersions/1";

const ENVIRONMENT = {
  ENVIRONMENT: "dev",
  GOOGLE_CLOUD_PROJECT: "p",
  SUBMISSION_BUCKET: "submissions",
  EVIDENCE_BUCKET: "evidence",
  APPROVAL_HEADS_BUCKET: "heads",
  APPROVAL_SIGNING_KEY_VERSION: KEY_VERSION,
  ADDON_ENDPOINT_URL: "https://ema-flow-dev-signer-1.europe-west4.run.app",
  IMAGE_DIGEST: `sha256:${"a".repeat(64)}`,
};

let mapping: EmaMapping;

beforeAll(async () => {
  mapping = await loadEmaMapping();
});

describe("the signer's configuration", () => {
  it("starts with no approver and no add-on: then it signs for no one", () => {
    const config = loadSignerConfig(ENVIRONMENT);
    expect(config.APPROVER_MAP_JSON).toBe("{}");
    expect([config.ADDON_SERVICE_ACCOUNT, config.ADDON_OAUTH_CLIENT_ID]).toEqual(["", ""]);
    expect(config.ALLOW_SYNTHETIC_SOURCES).toBe(false);
  });

  it("refuses a key that is not a version, an unknown environment or no image digest", () => {
    expect(() =>
      loadSignerConfig({
        ...ENVIRONMENT,
        APPROVAL_SIGNING_KEY_VERSION: KEY_VERSION.split("/cryptoKeyVersions/")[0],
      }),
    ).toThrow();
    expect(() => loadSignerConfig({ ...ENVIRONMENT, ENVIRONMENT: "staging" })).toThrow();
    expect(() => loadSignerConfig({ ...ENVIRONMENT, IMAGE_DIGEST: undefined })).toThrow();
  });

  it("builds an app that answers its health check and refuses an unverified caller", async () => {
    const kms = {
      asymmetricSign: () => Promise.reject(new Error("unused")),
      getPublicKey: () => Promise.reject(new Error("unused")),
    };
    const app = buildSignerApp(loadSignerConfig(ENVIRONMENT), mapping, kms, new OAuth2Client());
    expect((await app.request("/healthz")).status).toBe(200);
    const refused = await app.request("/", { method: "POST", body: JSON.stringify({}) });
    expect([refused.status, await refused.json()]).toEqual([401, { error: "unauthenticated" }]);
  });
});

describe("signing with Cloud KMS", () => {
  const message = Buffer.from("statement bytes", "utf8");
  const digest = createHash("sha256").update(message).digest();
  const signature = Buffer.from("signature bytes");

  function kms(answer: Record<string, unknown>) {
    const requests: unknown[] = [];
    return {
      requests,
      asymmetricSign: (request: unknown) => {
        requests.push(request);
        return Promise.resolve([
          {
            name: KEY_VERSION,
            signature,
            verifiedDigestCrc32c: true,
            signatureCrc32c: { value: String(crc32c(signature)) },
            ...answer,
          },
        ]);
      },
    };
  }

  it("sends the message's SHA-256 with its checksum, to the configured version", async () => {
    const client = kms({});
    expect(await kmsSigner(client as never, KEY_VERSION)(message)).toEqual(signature);
    expect(client.requests).toEqual([
      { name: KEY_VERSION, digest: { sha256: digest }, digestCrc32c: { value: crc32c(digest) } },
    ]);
  });

  it.each([
    ["no signature", { signature: undefined }, "no approval signature"],
    ["an unverified digest", { verifiedDigestCrc32c: false }, "did not sign the approval digest"],
    ["another version", { name: `${KEY_VERSION}0` }, "did not sign the approval digest"],
    ["a failed checksum", { signatureCrc32c: { value: "1" } }, "fails its checksum"],
  ])("refuses %s", async (_case, answer, error) => {
    await expect(kmsSigner(kms(answer) as never, KEY_VERSION)(message)).rejects.toThrow(error);
  });
});
