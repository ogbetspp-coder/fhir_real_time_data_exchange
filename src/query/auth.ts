import { OAuth2Client } from "google-auth-library";

import { PrincipalId } from "../contracts/common.js";

// Authentication for the query service. Cloud Run authenticates the caller at the edge and the
// service verifies the same token again itself: the edge is not trusted alone (design note,
// "Phase 1 as built"). Nothing here logs, returns, or embeds the token or any part of it — a
// token is a credential, and a rejection reason derived from it is a side channel.

export type IdTokenVerifier = {
  // The verified principal (the token's `sub`), or undefined for any token that is missing,
  // malformed, expired, wrongly-audienced, or not Google-signed. The caller learns nothing more.
  verify(idToken: string): Promise<string | undefined>;
};

const GOOGLE_ISSUERS = new Set(["https://accounts.google.com", "accounts.google.com"]);

const BEARER = /^Bearer[ ]([\x21-\x7e]+)$/i;

export function bearerToken(authorization: string | undefined): string | undefined {
  if (authorization === undefined) return undefined;
  const match = BEARER.exec(authorization);
  return match?.[1];
}

export function googleIdTokenVerifier(
  audience: string,
  client: OAuth2Client = new OAuth2Client(),
): IdTokenVerifier {
  return {
    async verify(idToken: string): Promise<string | undefined> {
      try {
        const ticket = await client.verifyIdToken({ idToken, audience });
        const payload = ticket.getPayload();
        if (payload === undefined) return undefined;
        if (!GOOGLE_ISSUERS.has(payload.iss)) return undefined;
        // The principal identifies an entitlement holder and is written to the audit record, so
        // it has to satisfy the contract's PrincipalId before it is used as either.
        const principal = PrincipalId.safeParse(payload.sub);
        return principal.success ? principal.data : undefined;
      } catch {
        // Verification failures carry token material in their messages; none of it escapes.
        return undefined;
      }
    },
  };
}
