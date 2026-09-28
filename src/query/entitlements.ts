import { z } from "zod";

import { AddressableFhirId, PrincipalId } from "../contracts/common.js";

// Who may read what. Phase 1 backs this with a Terraform-managed map parsed once at startup;
// Firestore replaces the backing in phase 2 without changing this interface. A principal with
// no entry has no entitlements at all, and outside a caller's entitlement every document is
// `document-not-found`: existence is never disclosed (design note, constraint 4).
//
// A phase 1 entitlement is a list of document Bundle ids per principal and nothing else. There
// is no organisation field: no authorization decision reads one, and a field that is parsed but
// never consulted would only look like a control. Organisation scoping arrives with the
// Firestore directory in phase 2, together with a document-to-organisation binding.

export type Entitlements = { bundles: readonly string[] };

export type EntitlementDirectory = {
  entitlementsFor(principal: string): Entitlements | undefined;
};

// Strict: a key this schema does not know (an `organisation` from an older map, say) is a
// configuration error and fails startup rather than being silently ignored. An entitled id is an
// `AddressableFhirId`, never `.` or `..`: every tool checks the entitlement before it reads, so
// no id that is not one URL path segment ever reaches the store URL. The published tool inputs
// still take a `FhirId` (query-tools 4.x), and a `..` asked for is answered as any id outside
// the entitlement is, `document-not-found`.
const EntitlementSchema = z.strictObject({
  bundles: z.array(AddressableFhirId).max(10_000),
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
      { bundles: [...entitlements.bundles] },
    ]),
  );

  return {
    entitlementsFor(principal: string): Entitlements | undefined {
      return directory.get(principal);
    },
  };
}
