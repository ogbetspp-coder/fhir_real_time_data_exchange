import type { OAuth2Client } from "google-auth-library";

import { PrincipalId } from "../contracts/common.js";
import type { CredentialType } from "../contracts/query-tools.js";
import { sha256Utf8 } from "../lib/hash.js";

// Authentication for the query service. Cloud Run authenticates the caller at the edge and the
// service verifies the credential again itself: the edge is not trusted alone (design note,
// "Phase 1 as built"). Nothing here logs, returns, or embeds the token or any part of it — a
// token is a credential, and a rejection reason derived from it is a side channel.
//
// Two credential kinds arrive on `Authorization: Bearer`:
// - an OpenID Connect ID token (three base64url segments), verified against QUERY_AUDIENCE;
// - anything else is treated as a Google OAuth 2.0 access token (the end user's token, as
//   Gemini Enterprise forwards it) and verified through Google's tokeninfo endpoint. It is
//   accepted only when QUERY_OAUTH_CLIENT_IDS names its `aud` (or `azp`).
// Both resolve to the token's `sub`, which is the principal.

export type VerifiedCredential = { principal: string; credentialType: CredentialType };

// Why a credential was refused, as a fixed category — never a message, and never anything
// derived from the token's bytes. A caller is still told only "unauthenticated"; this is for the
// operator's log, and only when QUERY_LOG_REJECTION_REASON is set (see app.ts). The categories
// distinguish the mistakes that actually happen: an ID token minted for the wrong audience, an
// access token from an OAuth client the service does not accept, and a token Google no longer
// recognises.
export type RejectionReason =
  | "no-bearer"
  | "id-token-rejected"
  | "access-tokens-not-accepted"
  | "access-token-audience"
  | "access-token-expired"
  | "access-token-lookup-failed"
  | "principal-malformed";

export type Rejected = { rejected: RejectionReason };

export function isRejected(result: VerifiedCredential | Rejected): result is Rejected {
  return "rejected" in result;
}

export type CredentialVerifier = {
  // The verified principal and how it was proven, or the category of refusal. The caller is
  // told only that it was not authenticated; the category never reaches it.
  verify(token: string): Promise<VerifiedCredential | Rejected>;
};

const GOOGLE_ISSUERS = new Set(["https://accounts.google.com", "accounts.google.com"]);

const BEARER = /^Bearer[ ]([\x21-\x7e]+)$/i;

// A JWT is exactly three base64url segments; a Google access token never is.
const JWT_SHAPE = /^[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+$/;

export function bearerToken(authorization: string | undefined): string | undefined {
  if (authorization === undefined) return undefined;
  const match = BEARER.exec(authorization);
  return match?.[1];
}

export function isJwtShaped(token: string): boolean {
  return JWT_SHAPE.test(token);
}

// Successful access-token verifications are kept for at most this long, and never past the
// token's own expiry, so one request per token reaches Google rather than one per tool call.
export const ACCESS_TOKEN_CACHE_MAX_MS = 300_000;
export const ACCESS_TOKEN_CACHE_MAX_ENTRIES = 1_000;

type CacheEntry = { principal: string; expiresAt: number };

export type GoogleVerifierOptions = {
  audience: string;
  // OAuth 2.0 client ids whose access tokens are accepted. Empty: access tokens are rejected.
  oauthClientIds: readonly string[];
  client: Pick<OAuth2Client, "verifyIdToken" | "getTokenInfo">;
  now?: () => number;
};

export function googleCredentialVerifier(options: GoogleVerifierOptions): CredentialVerifier {
  const { audience, client } = options;
  const clientIds = new Set(options.oauthClientIds);
  const now = options.now ?? Date.now;
  // Keyed by the token's SHA-256, never by the token; the key never leaves this map. Insertion
  // order is eviction order.
  const cache = new Map<string, CacheEntry>();

  async function verifyIdToken(idToken: string): Promise<VerifiedCredential | Rejected> {
    try {
      const ticket = await client.verifyIdToken({ idToken, audience });
      const payload = ticket.getPayload();
      if (payload === undefined) return { rejected: "id-token-rejected" };
      if (!GOOGLE_ISSUERS.has(payload.iss)) return { rejected: "id-token-rejected" };
      // The principal identifies an entitlement holder and is written to the audit record, so
      // it has to satisfy the contract's PrincipalId before it is used as either.
      const principal = PrincipalId.safeParse(payload.sub);
      return principal.success
        ? { principal: principal.data, credentialType: "id-token" }
        : { rejected: "principal-malformed" };
    } catch {
      // Verification failures carry token material in their messages; none of it escapes.
      return { rejected: "id-token-rejected" };
    }
  }

  async function verifyAccessToken(accessToken: string): Promise<VerifiedCredential | Rejected> {
    if (clientIds.size === 0) return { rejected: "access-tokens-not-accepted" };

    const key = sha256Utf8(accessToken);
    const cached = cache.get(key);
    const at = now();
    if (cached !== undefined) {
      if (cached.expiresAt > at) {
        return { principal: cached.principal, credentialType: "access-token" };
      }
      cache.delete(key);
    }

    try {
      // getTokenInfo sends the token in the Authorization header of a POST to Google's tokeninfo
      // endpoint; it is never placed in a URL here.
      const info = await client.getTokenInfo(accessToken);
      if (typeof info.expiry_date !== "number" || !(info.expiry_date > at)) {
        return { rejected: "access-token-expired" };
      }
      const presentedTo = [info.aud, info.azp].filter((id): id is string => typeof id === "string");
      if (!presentedTo.some((id) => clientIds.has(id)))
        return { rejected: "access-token-audience" };
      const principal = PrincipalId.safeParse(info.sub);
      if (!principal.success) return { rejected: "principal-malformed" };

      const expiresAt = Math.min(info.expiry_date, at + ACCESS_TOKEN_CACHE_MAX_MS);
      if (cache.size >= ACCESS_TOKEN_CACHE_MAX_ENTRIES) {
        const oldest = cache.keys().next().value;
        if (oldest !== undefined) cache.delete(oldest);
      }
      cache.set(key, { principal: principal.data, expiresAt });
      return { principal: principal.data, credentialType: "access-token" };
    } catch {
      // A failed lookup is never cached and its message (which may quote the token) never escapes.
      return { rejected: "access-token-lookup-failed" };
    }
  }

  return {
    verify(token: string): Promise<VerifiedCredential | Rejected> {
      return isJwtShaped(token) ? verifyIdToken(token) : verifyAccessToken(token);
    },
  };
}
