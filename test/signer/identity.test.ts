import { createSign, generateKeyPairSync } from "node:crypto";

import { OAuth2Client } from "google-auth-library";
import { describe, expect, it } from "vitest";

import { googleAddOnIdentity, USER_TOKEN_MAX_AGE_SECONDS } from "../../src/signer/identity.js";
import { sha256Utf8 } from "../../src/lib/hash.js";

// The signer's verification of Google-signed tokens (docs/design/approval.md, D2), against locally
// generated keys and tokens: what Google's certificates and tokens would be, made here. Build step
// 1's spike checks the same against the real Workspace add-on.

const ENDPOINT = "https://ema-flow-dev-signer-123456789012.europe-west4.run.app";
const ADDON_ACCOUNT = "service-123456789012@gcp-sa-gsuiteaddons.iam.gserviceaccount.com";
const CLIENT_ID = "123456789012-addon.apps.googleusercontent.com";

const google = generateKeyPairSync("rsa", { modulusLength: 2_048 });
const impostor = generateKeyPairSync("rsa", { modulusLength: 2_048 });
const KID = "local-test-key";

function base64url(value: unknown): string {
  return Buffer.from(JSON.stringify(value)).toString("base64url");
}

function token(claims: Record<string, unknown>, key = google.privateKey, kid = KID): string {
  const signing = `${base64url({ alg: "RS256", kid, typ: "JWT" })}.${base64url(claims)}`;
  const signature = createSign("RSA-SHA256").update(signing).sign(key).toString("base64url");
  return `${signing}.${signature}`;
}

const now = Math.floor(Date.now() / 1_000);

function systemToken(overrides: Record<string, unknown> = {}): string {
  return token({
    iss: "https://accounts.google.com",
    aud: ENDPOINT,
    sub: "111111111111111111111",
    email: ADDON_ACCOUNT,
    email_verified: true,
    iat: now,
    exp: now + 3_600,
    ...overrides,
  });
}

function userToken(overrides: Record<string, unknown> = {}): string {
  return token({
    iss: "https://accounts.google.com",
    aud: CLIENT_ID,
    sub: "109876543210987654321",
    email: "approver@khs.dev",
    email_verified: true,
    name: "Synthetic Approver",
    iat: now,
    exp: now + 3_600,
    ...overrides,
  });
}

function verifier(overrides: { addOnServiceAccount?: string; oauthClientId?: string } = {}) {
  return googleAddOnIdentity({
    endpointUrl: ENDPOINT,
    addOnServiceAccount: ADDON_ACCOUNT,
    oauthClientId: CLIENT_ID,
    certificates: () =>
      Promise.resolve({
        [KID]: google.publicKey.export({ type: "spki", format: "pem" }).toString(),
      }),
    client: new OAuth2Client(),
    ...overrides,
  });
}

describe("the approver's identity, as Google asserts it", () => {
  it("is the user token's subject, e-mail and name, once both tokens verify", async () => {
    const user = userToken();
    expect(await verifier().verify(systemToken(), user)).toEqual({
      approver: {
        sub: "109876543210987654321",
        email: "approver@khs.dev",
        name: "Synthetic Approver",
      },
      userTokenSha256: sha256Utf8(user),
    });
  });

  it("is refused until the add-on is configured", async () => {
    expect(await verifier({ addOnServiceAccount: "" }).verify(systemToken(), userToken())).toBe(
      "addon-not-configured",
    );
    expect(await verifier({ oauthClientId: "" }).verify(systemToken(), userToken())).toBe(
      "addon-not-configured",
    );
  });

  it("refuses a call that does not come from the add-on", async () => {
    const check = verifier();
    expect(await check.verify(undefined, userToken())).toBe("no-system-token");
    expect(await check.verify(systemToken({ aud: "https://elsewhere.example" }), userToken())).toBe(
      "system-token-rejected",
    );
    const signedByAnother = token(
      {
        iss: "https://accounts.google.com",
        aud: ENDPOINT,
        email: ADDON_ACCOUNT,
        iat: now,
        exp: now + 60,
      },
      impostor.privateKey,
    );
    expect(await check.verify(signedByAnother, userToken())).toBe("system-token-rejected");
    expect(
      await check.verify(
        systemToken({ email: "someone@sage-ship-509104-b8.iam.gserviceaccount.com" }),
        userToken(),
      ),
    ).toBe("system-token-not-addon");
    expect(await check.verify(systemToken({ iss: "https://evil.example" }), userToken())).toBe(
      "system-token-rejected",
    );
  });

  it("refuses a user token for another client, by another key, expired or not fresh", async () => {
    const check = verifier();
    expect(await check.verify(systemToken(), undefined)).toBe("no-user-token");
    expect(await check.verify(systemToken(), userToken({ aud: "another-client" }))).toBe(
      "user-token-rejected",
    );
    const forged = token(
      { iss: "https://accounts.google.com", aud: CLIENT_ID, sub: "1", iat: now, exp: now + 60 },
      impostor.privateKey,
    );
    expect(await check.verify(systemToken(), forged)).toBe("user-token-rejected");
    expect(
      await check.verify(systemToken(), userToken({ iat: now - 7_200, exp: now - 3_600 })),
    ).toBe("user-token-rejected");
    expect(
      await check.verify(
        systemToken(),
        userToken({ iat: now - USER_TOKEN_MAX_AGE_SECONDS - 60, exp: now + 3_000 }),
      ),
    ).toBe("user-token-stale");
  });

  it("refuses a user whose e-mail is unverified, or whose name or e-mail is absent", async () => {
    const check = verifier();
    expect(await check.verify(systemToken(), userToken({ email_verified: false }))).toBe(
      "user-email-unverified",
    );
    expect(await check.verify(systemToken(), userToken({ name: undefined }))).toBe(
      "user-claims-missing",
    );
    expect(await check.verify(systemToken(), userToken({ email: undefined }))).toBe(
      "user-claims-missing",
    );
  });
});
