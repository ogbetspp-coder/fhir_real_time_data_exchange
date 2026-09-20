import type { AddressInfo } from "node:net";
import type { Server } from "node:http";

import type { LoginTicket, TokenInfo, TokenPayload } from "google-auth-library";
import { afterAll, beforeAll, describe, expect, it, vi } from "vitest";

import { loadEmaMapping } from "../../src/fhir/mapping.js";
import { sha256Utf8 } from "../../src/lib/hash.js";
import { createQueryServer } from "../../src/query/app.js";
import {
  ACCESS_TOKEN_CACHE_MAX_ENTRIES,
  ACCESS_TOKEN_CACHE_MAX_MS,
  googleCredentialVerifier,
  isJwtShaped,
  type GoogleVerifierOptions,
} from "../../src/query/auth.js";
import {
  PRINCIPAL_A,
  SERVICE_VERSION,
  buildQueryStore,
  createFakeReader,
  entitlementDirectory,
  type QueryStore,
} from "./fixtures.js";

// The real verifier over a stubbed OAuth2Client: which path a credential takes, what each path
// requires, what the access-token cache does, and — through the real HTTP service — that no
// log line ever carries a token or its hash.

const AUDIENCE = "https://query.ema-flow.invalid";
const CLIENT_ID = "123456789012-abcdefghijklmnop.apps.googleusercontent.com";
const OTHER_CLIENT_ID = "999999999999-zzzzzzzzzzzzzzzz.apps.googleusercontent.com";
const NOW = Date.parse("2026-09-20T12:00:00.000Z");

// Three base64url segments; the content is irrelevant because the stub decides its fate.
function jwt(marker: string): string {
  return `eyJhbGciOiJSUzI1NiJ9.${Buffer.from(marker).toString("base64url")}.c2lnbmF0dXJl`;
}

type Stub = {
  client: GoogleVerifierOptions["client"];
  idTokens: Map<string, TokenPayload>;
  accessTokens: Map<string, TokenInfo>;
  calls: { verifyIdToken: number; getTokenInfo: number };
};

function stubClient(): Stub {
  const idTokens = new Map<string, TokenPayload>();
  const accessTokens = new Map<string, TokenInfo>();
  const calls = { verifyIdToken: 0, getTokenInfo: 0 };
  const client = {
    verifyIdToken(options: { idToken: string; audience?: string | string[] }) {
      calls.verifyIdToken += 1;
      const payload = idTokens.get(options.idToken);
      if (payload === undefined || payload.aud !== options.audience) {
        return Promise.reject(new Error(`rejected ${options.idToken}`));
      }
      return Promise.resolve({ getPayload: () => payload } as LoginTicket);
    },
    getTokenInfo(accessToken: string) {
      calls.getTokenInfo += 1;
      const info = accessTokens.get(accessToken);
      if (info === undefined) return Promise.reject(new Error(`invalid_token ${accessToken}`));
      return Promise.resolve(info);
    },
  } as unknown as GoogleVerifierOptions["client"];
  return { client, idTokens, accessTokens, calls };
}

function idPayload(sub: string, aud = AUDIENCE, iss = "https://accounts.google.com"): TokenPayload {
  return { iss, sub, aud, iat: NOW / 1000 - 60, exp: NOW / 1000 + 3600 };
}

function accessInfo(sub: string, overrides: Partial<TokenInfo> = {}): TokenInfo {
  return {
    aud: CLIENT_ID,
    sub,
    scopes: ["openid"],
    expiry_date: NOW + 3_600_000,
    ...overrides,
  };
}

function verifierWith(
  stub: Stub,
  overrides: Partial<GoogleVerifierOptions> = {},
  clock: { now: number } = { now: NOW },
) {
  return googleCredentialVerifier({
    audience: AUDIENCE,
    oauthClientIds: [CLIENT_ID],
    client: stub.client,
    now: () => clock.now,
    ...overrides,
  });
}

describe("credential verification", () => {
  it("routes a JWT-shaped bearer to ID-token verification and anything else to tokeninfo", async () => {
    const stub = stubClient();
    const id = jwt("user-a");
    stub.idTokens.set(id, idPayload(PRINCIPAL_A));
    stub.accessTokens.set("ya29.opaque-access-token", accessInfo(PRINCIPAL_A));
    const verifier = verifierWith(stub);

    expect(isJwtShaped(id)).toBe(true);
    expect(isJwtShaped("ya29.opaque-access-token")).toBe(false);

    expect(await verifier.verify(id)).toEqual({
      principal: PRINCIPAL_A,
      credentialType: "id-token",
    });
    expect(stub.calls).toEqual({ verifyIdToken: 1, getTokenInfo: 0 });

    expect(await verifier.verify("ya29.opaque-access-token")).toEqual({
      principal: PRINCIPAL_A,
      credentialType: "access-token",
    });
    expect(stub.calls).toEqual({ verifyIdToken: 1, getTokenInfo: 1 });
  });

  it("rejects an ID token for another audience or issuer, and one whose sub is not a principal id", async () => {
    const stub = stubClient();
    const otherAudience = jwt("other-aud");
    const otherIssuer = jwt("other-iss");
    const badSub = jwt("bad-sub");
    stub.idTokens.set(otherAudience, idPayload(PRINCIPAL_A, "https://elsewhere.invalid"));
    stub.idTokens.set(otherIssuer, idPayload(PRINCIPAL_A, AUDIENCE, "https://issuer.invalid"));
    stub.idTokens.set(badSub, idPayload("someone@example.com"));
    const verifier = verifierWith(stub);

    expect(await verifier.verify(otherAudience)).toBeUndefined();
    expect(await verifier.verify(otherIssuer)).toBeUndefined();
    expect(await verifier.verify(badSub)).toBeUndefined();
    expect(await verifier.verify(jwt("unknown"))).toBeUndefined();
  });

  // The demo path. `gcloud auth print-identity-token --audiences=...` is refused for a user
  // account, and a user's plain identity token carries gcloud's own OAuth client id as its
  // audience, so a human presenting the demo with their own Google account has only
  // `gcloud auth print-access-token`: an opaque user access token whose tokeninfo `aud`/`azp`
  // is the client id of the gcloud installation that minted it. The client id below is a test
  // value, not a value this repository configures anywhere.
  it("accepts a user access token whose tokeninfo names a configured client id", async () => {
    const GCLOUD_LIKE_CLIENT_ID = "32555940559-test.apps.googleusercontent.com";
    const stub = stubClient();
    const userToken = "ya29.a0AfB_gcloud-user-access-token";
    // What Google's tokeninfo returns for a user access token minted by a gcloud login.
    stub.accessTokens.set(userToken, {
      aud: GCLOUD_LIKE_CLIENT_ID,
      azp: GCLOUD_LIKE_CLIENT_ID,
      sub: PRINCIPAL_A,
      email: "person@example.invalid",
      scopes: ["openid", "https://www.googleapis.com/auth/cloud-platform"],
      expiry_date: NOW + 3_600_000,
    });

    // Not configured: the operator has not opted into the path, and the token is refused.
    expect(await verifierWith(stub).verify(userToken)).toBeUndefined();

    // Configured: the same token resolves to the user's Google subject, which is the principal
    // an operator entitles. Nothing else about the path differs from the ID-token path.
    const opened = verifierWith(stub, { oauthClientIds: [GCLOUD_LIKE_CLIENT_ID] });
    expect(await opened.verify(userToken)).toEqual({
      principal: PRINCIPAL_A,
      credentialType: "access-token",
    });
    // The bearer is not JWT-shaped, so it never reaches ID-token verification.
    expect(isJwtShaped(userToken)).toBe(false);
    expect(stub.calls.verifyIdToken).toBe(0);
  });

  it("rejects an access token presented to a client id that is not configured", async () => {
    const stub = stubClient();
    stub.accessTokens.set("ya29.wrong-aud", accessInfo(PRINCIPAL_A, { aud: OTHER_CLIENT_ID }));
    stub.accessTokens.set(
      "ya29.azp-ok",
      accessInfo(PRINCIPAL_A, { aud: OTHER_CLIENT_ID, azp: CLIENT_ID }),
    );
    const verifier = verifierWith(stub);

    expect(await verifier.verify("ya29.wrong-aud")).toBeUndefined();
    // `azp` naming a configured client is accepted, as `aud` is.
    expect(await verifier.verify("ya29.azp-ok")).toEqual({
      principal: PRINCIPAL_A,
      credentialType: "access-token",
    });
  });

  it("rejects an expired access token and one without a sub", async () => {
    const stub = stubClient();
    stub.accessTokens.set("ya29.expired", accessInfo(PRINCIPAL_A, { expiry_date: NOW - 1 }));
    stub.accessTokens.set("ya29.no-sub", {
      aud: CLIENT_ID,
      scopes: ["openid"],
      expiry_date: NOW + 3_600_000,
    });
    const verifier = verifierWith(stub);

    expect(await verifier.verify("ya29.expired")).toBeUndefined();
    expect(await verifier.verify("ya29.no-sub")).toBeUndefined();
    // Failures are never cached: the next presentation asks Google again.
    expect(await verifier.verify("ya29.expired")).toBeUndefined();
    expect(stub.calls.getTokenInfo).toBe(3);
  });

  it("rejects every access token when no OAuth client id is configured, without asking Google", async () => {
    const stub = stubClient();
    stub.accessTokens.set("ya29.valid", accessInfo(PRINCIPAL_A));
    const verifier = verifierWith(stub, { oauthClientIds: [] });

    expect(await verifier.verify("ya29.valid")).toBeUndefined();
    expect(stub.calls.getTokenInfo).toBe(0);

    // The ID-token path is unaffected by the access-token posture.
    const id = jwt("user-a");
    stub.idTokens.set(id, idPayload(PRINCIPAL_A));
    expect(await verifier.verify(id)).toEqual({
      principal: PRINCIPAL_A,
      credentialType: "id-token",
    });
  });

  it("serves a repeated access token from the cache until its bounded lifetime ends", async () => {
    const stub = stubClient();
    stub.accessTokens.set("ya29.cached", accessInfo(PRINCIPAL_A));
    const clock = { now: NOW };
    const verifier = verifierWith(stub, {}, clock);

    expect(await verifier.verify("ya29.cached")).toMatchObject({ principal: PRINCIPAL_A });
    expect(await verifier.verify("ya29.cached")).toMatchObject({ principal: PRINCIPAL_A });
    expect(stub.calls.getTokenInfo).toBe(1);

    // Just inside the cache lifetime: still served from memory.
    clock.now = NOW + ACCESS_TOKEN_CACHE_MAX_MS - 1;
    expect(await verifier.verify("ya29.cached")).toMatchObject({ principal: PRINCIPAL_A });
    expect(stub.calls.getTokenInfo).toBe(1);

    // At the lifetime: Google is asked again, even though the token itself is still valid.
    clock.now = NOW + ACCESS_TOKEN_CACHE_MAX_MS;
    expect(await verifier.verify("ya29.cached")).toMatchObject({ principal: PRINCIPAL_A });
    expect(stub.calls.getTokenInfo).toBe(2);
  });

  it("caches no longer than the token's own remaining lifetime", async () => {
    const stub = stubClient();
    stub.accessTokens.set("ya29.short", accessInfo(PRINCIPAL_A, { expiry_date: NOW + 10_000 }));
    const clock = { now: NOW };
    const verifier = verifierWith(stub, {}, clock);

    expect(await verifier.verify("ya29.short")).toMatchObject({ principal: PRINCIPAL_A });
    clock.now = NOW + 10_000;
    // The cached entry has expired with the token; the lookup goes to Google, which (in this
    // stub) still reports the old expiry, so the token is now rejected rather than served.
    expect(await verifier.verify("ya29.short")).toBeUndefined();
    expect(stub.calls.getTokenInfo).toBe(2);
  });

  it("bounds the cache and evicts the oldest entry first", async () => {
    const stub = stubClient();
    const tokens = Array.from(
      { length: ACCESS_TOKEN_CACHE_MAX_ENTRIES + 1 },
      (_, position) => `ya29.token-${String(position)}`,
    );
    for (const token of tokens) stub.accessTokens.set(token, accessInfo(PRINCIPAL_A));
    const verifier = verifierWith(stub);

    for (const token of tokens) await verifier.verify(token);
    expect(stub.calls.getTokenInfo).toBe(tokens.length);

    // The newest is cached; the oldest was evicted to make room for it.
    await verifier.verify(tokens[tokens.length - 1] ?? "");
    expect(stub.calls.getTokenInfo).toBe(tokens.length);
    await verifier.verify(tokens[0] ?? "");
    expect(stub.calls.getTokenInfo).toBe(tokens.length + 1);
  });
});

describe("credential verification through the HTTP service", () => {
  let store: QueryStore;
  let server: Server;
  let origin: string;
  const stub = stubClient();

  beforeAll(async () => {
    store = buildQueryStore(await loadEmaMapping());
    server = createQueryServer({
      reader: createFakeReader(store.documents).reader,
      mapping: store.mapping,
      serviceVersion: SERVICE_VERSION,
      verifier: verifierWith(stub, {}, { now: Date.now() }),
      entitlements: entitlementDirectory(store),
      audit: () => undefined,
    });
    await new Promise<void>((resolve) => {
      server.listen(0, "127.0.0.1", resolve);
    });
    origin = `http://127.0.0.1:${String((server.address() as AddressInfo).port)}`;
  });

  afterAll(async () => {
    server.closeAllConnections();
    await new Promise<void>((resolve) => {
      server.close(() => {
        resolve();
      });
    });
  });

  it("never writes a token or its hash to a log line, on any path", async () => {
    const id = jwt("user-a");
    const access = "ya29.a0AfH6SMBopaque-access-token-value";
    const rejected = "ya29.rejected-access-token";
    const badJwt = jwt("rejected-id-token");
    stub.idTokens.set(id, idPayload(PRINCIPAL_A));
    stub.accessTokens.set(access, accessInfo(PRINCIPAL_A, { expiry_date: Date.now() + 3_600_000 }));

    const lines: string[] = [];
    const push = (...args: unknown[]): void => {
      for (const argument of args) {
        lines.push(typeof argument === "string" ? argument : JSON.stringify(argument));
      }
    };
    const outSpy = vi.spyOn(console, "log").mockImplementation(push);
    const errSpy = vi.spyOn(console, "error").mockImplementation(push);
    const statuses: number[] = [];
    try {
      for (const bearer of [id, access, rejected, badJwt]) {
        const response = await fetch(`${origin}/mcp`, {
          method: "POST",
          headers: {
            "content-type": "application/json",
            accept: "application/json, text/event-stream",
            authorization: `Bearer ${bearer}`,
          },
          body: JSON.stringify({
            jsonrpc: "2.0",
            id: 1,
            method: "tools/call",
            params: { name: "find_product", arguments: { query: "synthetic" } },
          }),
        });
        statuses.push(response.status);
        await response.body?.cancel();
      }
    } finally {
      outSpy.mockRestore();
      errSpy.mockRestore();
    }

    expect(statuses).toEqual([200, 200, 401, 401]);
    // Both refusals were logged, so the scan below runs over real lines.
    expect(lines.filter((line) => line.includes('"event":"unauthenticated"'))).toHaveLength(2);
    const logged = lines.join("\n");
    for (const secret of [id, access, rejected, badJwt]) {
      expect([secret, logged.includes(secret)]).toEqual([secret, false]);
      expect([secret, logged.includes(sha256Utf8(secret))]).toEqual([secret, false]);
    }
    expect(logged.includes("Bearer")).toBe(false);
  });
});
