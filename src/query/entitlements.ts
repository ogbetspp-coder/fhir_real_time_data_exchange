import { z } from "zod";

import { FhirId, PrincipalId, Token } from "../contracts/common.js";

// Who may read what. Phase 1 backs this with a Terraform-managed map parsed once at startup;
// Firestore replaces the backing in phase 2 without changing this interface. A principal with
// no entry has no entitlements at all, and outside a caller's entitlement every document is
// `document-not-found`: existence is never disclosed (design note, constraint 4).

export type Entitlements = { organisation: string; bundles: readonly string[] };

export type EntitlementDirectory = {
  entitlementsFor(principal: string): Entitlements | undefined;
};

// `organisation` is an identifier, not prose: it is compared, never displayed as narrative, and
// it must not be able to carry text into a log line.
const EntitlementSchema = z.strictObject({
  organisation: Token,
  bundles: z.array(FhirId).max(10_000),
});

const DirectorySchema = z.record(PrincipalId, EntitlementSchema);

export function parseEntitlements(json: string): EntitlementDirectory {
  let parsed: unknown;
  try {
    parsed = JSON.parse(json);
  } catch {
    // The value may hold principal identifiers; the message names the variable and nothing else.
    throw new Error("QUERY_ENTITLEMENTS_JSON is not valid JSON");
  }

  const result = DirectorySchema.safeParse(parsed);
  if (!result.success) {
    throw new Error(
      `QUERY_ENTITLEMENTS_JSON is not a valid entitlement map (${String(result.error.issues.length)} issues)`,
    );
  }

  const directory = new Map<string, Entitlements>(
    Object.entries(result.data).map(([principal, entitlements]) => [
      principal,
      { organisation: entitlements.organisation, bundles: [...entitlements.bundles] },
    ]),
  );

  return {
    entitlementsFor(principal: string): Entitlements | undefined {
      return directory.get(principal);
    },
  };
}
