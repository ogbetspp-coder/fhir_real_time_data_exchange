import { readFileSync } from "node:fs";

import { describe, expect, it } from "vitest";

// Terraform refuses inputs the services would refuse, or that would publish them (audit I-8).
// These inputs are repository variables anyone with write access can change, and the deploy
// applies them unattended. CI has no Terraform, so the conditions are pinned here as written; each
// was also exercised with local plans (the pull request that added them lists the cases).

const variables = readFileSync("infra/variables.tf", "utf8");
const variable = (name: string) =>
  new RegExp(`^variable "${name}" \\{\\n([\\s\\S]*?)^\\}`, "m").exec(variables)?.[1] ?? "";

describe("query_invokers", () => {
  it("accepts only named users, groups and service accounts, as query_token_creators does", () => {
    const member = 'can(regex("^(user|group|serviceAccount):[^\\\\s]+$", member))';
    expect(variable("query_invokers")).toContain(member);
    expect(variable("query_token_creators")).toContain(member);
    expect(variable("query_invokers")).toContain(
      "allUsers and allAuthenticatedUsers are not accepted",
    );
  });
});

describe("query_entitlements_json", () => {
  const body = variable("query_entitlements_json");

  it("reads each bundle's type from its own JSON, since regex() and tolist() convert numbers", () => {
    expect(body).toContain('startswith(jsonencode(bundle), "\\"")');
    // Iterated as decoded, not through tolist(), which would turn [5] into ["5"] first.
    expect(body).toMatch(/for bundle in entitlement\.bundles :/);
  });

  it("caps each entitlement at the service's 10,000 bundles", () => {
    expect(body).toContain("try(length(entitlement.bundles), 10001) <= 10000");
    expect(readFileSync("src/query/entitlements.ts", "utf8")).toContain(
      "bundles: z.array(AddressableFhirId).max(10_000)",
    );
  });

  // Each id is a single URL path segment, as the service requires at startup: never . or ..
  // (src/query/entitlements.ts).
  it("takes only ids that begin with a letter or a digit", () => {
    expect(body).toContain('can(regex("^[A-Za-z0-9][A-Za-z0-9.-]{0,63}$", bundle))');
  });
});

describe("alert_notification_channels", () => {
  it("accepts only channel resource names", () => {
    expect(variable("alert_notification_channels")).toContain(
      'can(regex("^projects/[^/\\\\s]+/notificationChannels/[^/\\\\s]+$", channel))',
    );
  });
});
