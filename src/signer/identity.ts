import type { OAuth2Client } from "google-auth-library";

import type { VerifiedApprover } from "../approval/approve.js";
import { sha256Utf8 } from "../lib/hash.js";

// Who clicked, as Google asserts it (docs/design/approval.md, D2 and "Which Google component
// asserts the approver", option B): a Google Chat app built as a Workspace add-on. Every event
// Google sends to the add-on's HTTP endpoint carries two Google-signed ID tokens, and the signer
// verifies both itself, from the raw tokens (Google, "Build a Google Workspace add-on using HTTP
// endpoints", read 2026-10-06):
//
// - the system ID token, in the request's `Authorization: Bearer` header: "An ID token for the
//   Google Workspace add-on's service account for a specific deployment. It can be used to verify
//   that the request comes from Google." Its audience is the HTTP endpoint URL, and its `email` is
//   the add-on's service account;
// - the user ID token, `authorizationEventObject.userIdToken`: "An end user ID token", verified
//   against "the client ID that was created" for the add-on (the Marketplace SDK's HTTP
//   Deployments tab, Authorization Resource, OAuth Client Id). Its `sub` is the approver and its
//   verified `email` must be the approver map's. Nothing else of it is used: a Google ID token
//   carries `name` only with the profile scope, so the name recorded with an approval is the
//   approver map's (src/approval/approve.ts).
//
// The interface below is what the rest of the signer depends on. Build step 1's spike tests the
// one implementation against the real products; the tests here use locally generated keys.

export type IdentityRefusal =
  | "no-system-token"
  | "system-token-rejected"
  | "system-token-not-addon"
  | "no-user-token"
  | "user-token-rejected"
  | "user-token-stale"
  | "user-email-unverified"
  | "user-claims-missing"
  | "addon-not-configured";

export type VerifiedEvent = {
  approver: VerifiedApprover;
  // The user ID token's SHA-256: the token is consumed once (src/signer/service.ts), by its hash,
  // never stored or logged itself.
  userTokenSha256: string;
};

export type ApproverIdentityVerifier = {
  verify(
    systemToken: string | undefined,
    userToken: string | undefined,
  ): Promise<VerifiedEvent | IdentityRefusal>;
};

// A user ID token is accepted only this long after it was issued: the click must be recent (D2,
// "a fresh issue time"). Whether Google mints a new token for every event is for the spike to
// show; if it does not, this window refuses a click made long after the token was minted.
export const USER_TOKEN_MAX_AGE_SECONDS = 300;

const GOOGLE_ISSUERS = ["https://accounts.google.com", "accounts.google.com"];

export type AddOnIdentityOptions = {
  endpointUrl: string;
  addOnServiceAccount: string;
  oauthClientId: string;
  // Google's signing certificates, by key id (OAuth2Client.getFederatedSignonCertsAsync in
  // production; locally generated ones in tests).
  certificates: () => Promise<Parameters<OAuth2Client["verifySignedJwtWithCertsAsync"]>[1]>;
  client: Pick<OAuth2Client, "verifySignedJwtWithCertsAsync">;
  now?: () => number;
};

type Claims = Record<string, unknown>;

export function googleAddOnIdentity(options: AddOnIdentityOptions): ApproverIdentityVerifier {
  const now = options.now ?? Date.now;

  // Signature, issuer, audience and expiry, by google-auth-library, whose messages quote the token:
  // none of them escapes, a failure is only its closed code.
  async function claims(token: string, audience: string): Promise<Claims | undefined> {
    try {
      const ticket = await options.client.verifySignedJwtWithCertsAsync(
        token,
        await options.certificates(),
        audience,
        GOOGLE_ISSUERS,
      );
      return ticket.getPayload() as Claims | undefined;
    } catch {
      return undefined;
    }
  }

  return {
    async verify(systemToken, userToken) {
      if (
        options.addOnServiceAccount === "" ||
        options.oauthClientId === "" ||
        options.endpointUrl === ""
      ) {
        return "addon-not-configured";
      }
      if (systemToken === undefined || systemToken === "") return "no-system-token";
      const system = await claims(systemToken, options.endpointUrl);
      if (system === undefined) return "system-token-rejected";
      // As Google's own sample checks it: the token's email is the add-on's service account.
      if (system.email !== options.addOnServiceAccount) return "system-token-not-addon";

      if (userToken === undefined || userToken === "") return "no-user-token";
      const user = await claims(userToken, options.oauthClientId);
      if (user === undefined) return "user-token-rejected";
      const issuedAt = typeof user.iat === "number" ? user.iat : Number.NaN;
      const age = now() / 1_000 - issuedAt;
      if (!(age <= USER_TOKEN_MAX_AGE_SECONDS)) return "user-token-stale";
      if (user.email_verified !== true) return "user-email-unverified";
      const { sub, email } = user;
      if (typeof sub !== "string" || typeof email !== "string") return "user-claims-missing";
      return { approver: { sub, email }, userTokenSha256: sha256Utf8(userToken) };
    },
  };
}
