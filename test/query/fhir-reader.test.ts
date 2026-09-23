import { describe, expect, it, vi } from "vitest";

import type { FhirResource } from "../../src/fhir/types.js";
import { PROVENANCE_PAGE_SIZE, latestProvenance } from "../../src/query/fhir-reader.js";

// A document with two approved versions has two Provenance resources, and `get_provenance`
// answers with one of them. Which one must be a property of this code, not of whichever order
// the store happened to return: before this, a re-seeded environment could answer differently
// for the same call and nothing in the repository said it was wrong. And it must be the one
// written with the current version, which is decided by when the store wrote it, not by the
// approval date the submission carried.

function provenance(id: string, lastUpdated?: string, recorded?: string): FhirResource {
  return {
    resourceType: "Provenance",
    id,
    ...(lastUpdated === undefined ? {} : { meta: { lastUpdated } }),
    ...(recorded === undefined ? {} : { recorded }),
  };
}

// Every ordering of the same set, so the answer cannot depend on arrival order.
function permutations<T>(items: readonly T[]): T[][] {
  if (items.length <= 1) return [[...items]];
  return items.flatMap((item, index) =>
    permutations([...items.slice(0, index), ...items.slice(index + 1)]).map((rest) => [
      item,
      ...rest,
    ]),
  );
}

describe("latestProvenance", () => {
  it("answers with the most recently written approval", () => {
    const v1 = provenance("13c749d3", "2026-09-19T00:00:00Z");
    const v2 = provenance("17774cb7", "2026-09-20T00:00:00Z");

    for (const ordering of permutations([v1, v2])) {
      expect(latestProvenance(ordering)?.id).toBe("17774cb7");
    }
  });

  it("goes by when the store wrote an approval, not by the approval date it carries", () => {
    // Version 2 approved before version 1 was, but published after it: version 2 is current,
    // and its approval is the one written with it.
    const v1 = provenance("v1-approval", "2026-09-20T10:00:00Z", "2026-09-19T12:00:00Z");
    const v2 = provenance("v2-approval", "2026-09-21T10:00:00Z", "2026-09-18T12:00:00Z");
    for (const ordering of permutations([v1, v2])) {
      expect(latestProvenance(ordering)?.id).toBe("v2-approval");
    }

    // And the reverse: version 1 republished after version 2 is current again, and its
    // approval — written again in the same transaction — is the answer, although version 2's
    // approval date is later.
    const v2Current = provenance("v2-approval", "2026-09-21T10:00:00Z", "2026-09-21T09:00:00Z");
    const v1Again = provenance("v1-approval", "2026-09-22T10:00:00Z", "2026-09-19T12:00:00Z");
    for (const ordering of permutations([v2Current, v1Again])) {
      expect(latestProvenance(ordering)?.id).toBe("v1-approval");
    }
  });

  it("breaks a tie by resource id, so a shared timestamp still has one answer", () => {
    const same = "2026-09-20T00:00:00Z";
    const candidates = [provenance("ccc", same), provenance("aaa", same), provenance("bbb", same)];

    for (const ordering of permutations(candidates)) {
      expect(latestProvenance(ordering)?.id).toBe("aaa");
    }
  });

  it("compares the instant named, not the way it is spelled", () => {
    // The same moment, written in two time zones, plus an hour that is later than both.
    const utc = provenance("utc", "2026-09-20T12:00:00Z");
    const offset = provenance("offset", "2026-09-20T14:00:00+02:00");
    const later = provenance("later", "2026-09-20T13:00:00Z");

    expect(latestProvenance([utc, offset, later])?.id).toBe("later");
    // Spelled-out string order would put "2026-09-20T14:00:00+02:00" last; the instant does not.
    expect(latestProvenance([utc, offset])?.id).toBe("offset");
  });

  it("ranks a resource with no usable write time last without discarding it", () => {
    const dated = provenance("dated", "2026-09-19T00:00:00Z");
    // A later approval date does not rescue a resource the store gave no write time.
    const undatedResource = provenance("undated", undefined, "2026-09-22T00:00:00Z");
    const unparseable = provenance("unparseable", "not a date");

    expect(latestProvenance([undatedResource, dated])?.id).toBe("dated");
    expect(latestProvenance([unparseable, dated])?.id).toBe("dated");
    // The only answer available is still an answer.
    expect(latestProvenance([undatedResource])?.id).toBe("undated");
    // Two undated resources still resolve to one, by id.
    expect(latestProvenance([provenance("zzz"), provenance("aaa")])?.id).toBe("aaa");
  });

  it("has no answer for an empty search result", () => {
    expect(latestProvenance([])).toBeUndefined();
  });
});

describe("the Provenance search the reader sends", () => {
  it("asks the store for the newest first over a bounded page", async () => {
    const searchSet = {
      resourceType: "Bundle",
      type: "searchset",
      entry: [
        // In the order the store sorted them (`-recorded`), which is not the write order.
        {
          fullUrl: "x",
          resource: provenance("approved-last", "2026-09-19T00:00:00Z", "2026-09-19T00:00:00Z"),
        },
        {
          fullUrl: "y",
          resource: provenance("written-last", "2026-09-20T00:00:00Z", "2026-09-18T00:00:00Z"),
        },
      ],
    };

    vi.resetModules();
    vi.doMock("google-auth-library", () => ({
      GoogleAuth: class {
        getAccessToken(): Promise<string> {
          return Promise.resolve("test-token");
        }
      },
    }));

    const requested: string[] = [];
    const fetchSpy = vi.fn((url: string) => {
      requested.push(url);
      return Promise.resolve(
        new Response(JSON.stringify(searchSet), {
          status: 200,
          headers: { "content-type": "application/fhir+json" },
        }),
      );
    });
    vi.stubGlobal("fetch", fetchSpy);

    try {
      const { HealthcareFhirReader } = await import("../../src/query/fhir-reader.js");
      const reader = new HealthcareFhirReader({
        GOOGLE_CLOUD_PROJECT: "p",
        GCP_LOCATION: "europe-west4",
        HEALTHCARE_DATASET_ID: "d",
        TARGET_FHIR_STORE_ID: "s",
      });

      const answer = await reader.findProvenanceForBundle("bundle-1");

      expect(requested).toHaveLength(1);
      const url = new URL(requested[0] ?? "");
      expect(url.searchParams.get("target")).toBe("Bundle/bundle-1");
      expect(url.searchParams.get("_sort")).toBe("-recorded");
      expect(url.searchParams.get("_count")).toBe(String(PROVENANCE_PAGE_SIZE));
      // The store's order is asked for, and then not relied upon: the most recently written
      // approval is the answer.
      expect(answer?.id).toBe("written-last");
    } finally {
      vi.unstubAllGlobals();
      vi.doUnmock("google-auth-library");
      vi.resetModules();
    }
  });
});
