import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { FhirResource } from "../../src/fhir/types.js";
import { sha256, sha256Utf8 } from "../../src/lib/hash.js";
import {
  FhirReadError,
  HealthcareFhirReader,
  PROVENANCE_PAGE_SIZE,
  latestProvenance,
} from "../../src/query/fhir-reader.js";

// The reader's credential comes from Application Default Credentials through google-auth-library;
// it is replaced here at that boundary, and `fetch` is stubbed, so nothing below touches a
// credential or the network.
const auth = vi.hoisted(() => {
  const state: { token: string | null } = { token: "test-token" };
  return state;
});

vi.mock("google-auth-library", () => ({
  GoogleAuth: class {
    getAccessToken(): Promise<string | null> {
      return Promise.resolve(auth.token);
    }
  },
}));

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

const OPTIONS = {
  GOOGLE_CLOUD_PROJECT: "p",
  GCP_LOCATION: "europe-west4",
  HEALTHCARE_DATASET_ID: "d",
  TARGET_FHIR_STORE_ID: "s",
};
const STORE =
  "https://healthcare.googleapis.com/v1/projects/p/locations/europe-west4/datasets/d/fhirStores/s/fhir";

type Sent = { url: string; init: RequestInit };

let sent: Sent[];
let answer: () => Response;

function respond(status: number, body: unknown, statusText = ""): () => Response {
  return () =>
    new Response(JSON.stringify(body), {
      status,
      statusText,
      headers: { "content-type": "application/fhir+json" },
    });
}

beforeEach(() => {
  auth.token = "test-token";
  sent = [];
  answer = respond(200, {});
  vi.stubGlobal(
    "fetch",
    vi.fn((url: string, init: RequestInit = {}) => {
      sent.push({ url, init });
      return Promise.resolve(answer());
    }),
  );
});

afterEach(() => {
  vi.unstubAllGlobals();
});

function only(): Sent {
  expect(sent).toHaveLength(1);
  const [request] = sent;
  if (request === undefined) throw new Error("no request was sent");
  return request;
}

const DOCUMENT = { resourceType: "Bundle", id: "bundle-1", type: "document", entry: [] };

describe("reading a document Bundle", () => {
  it("GETs the Bundle from the validated store with read-only headers, the id encoded", async () => {
    answer = respond(200, DOCUMENT);
    const reader = new HealthcareFhirReader({ ...OPTIONS, HEALTHCARE_DATASET_ID: "d/x" });

    expect(await reader.readBundle("bundle/1")).toEqual(DOCUMENT);

    const request = only();
    expect(request.url).toBe(
      "https://healthcare.googleapis.com/v1/projects/p/locations/europe-west4/datasets/d%2Fx/fhirStores/s/fhir/Bundle/bundle%2F1",
    );
    expect(request.init.method).toBe("GET");
    expect(request.init.body).toBeUndefined();
    const headers = new Headers(request.init.headers);
    expect(headers.get("authorization")).toBe("Bearer test-token");
    expect(headers.get("accept")).toBe("application/fhir+json; charset=utf-8");
    expect(headers.get("x-request-id")).toMatch(
      /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/,
    );
    expect(headers.get("x-goog-healthcare-audit-appname")).toBe("ema-flow-query");
    expect(headers.get("x-goog-healthcare-audit-reason")).toBe("ePI read-only query service");
  });

  it("GETs one version through _history", async () => {
    answer = respond(200, DOCUMENT);
    const reader = new HealthcareFhirReader(OPTIONS);

    expect(await reader.readBundleVersion("bundle-1", "MTc1ODQ/x")).toEqual(DOCUMENT);
    expect(only().url).toBe(`${STORE}/Bundle/bundle-1/_history/MTc1ODQ%2Fx`);
  });

  // A missing resource is not an error: the tools answer `document-not-found` and learn
  // nothing else about the store.
  it.each([404, 410])("answers undefined, not an error, for %i", async (status) => {
    answer = respond(status, { resourceType: "OperationOutcome", issue: [{ code: "not-found" }] });
    const reader = new HealthcareFhirReader(OPTIONS);

    expect(await reader.readBundle("bundle-1")).toBeUndefined();
    expect(await reader.readBundleVersion("bundle-1", "1")).toBeUndefined();
    expect(await reader.findProvenanceForBundle("bundle-1")).toBeUndefined();
  });

  it("raises FhirReadError on any other refusal, naming the body by hash and issue count only", async () => {
    const body = {
      resourceType: "OperationOutcome",
      issue: [
        { severity: "error", code: "forbidden", diagnostics: "quoted narrative: take two tablets" },
        { severity: "error", code: "processing" },
      ],
    };
    answer = respond(403, body, "Forbidden");
    const reader = new HealthcareFhirReader(OPTIONS);

    const error = await reader.readBundle("bundle-1").catch((e: unknown) => e);

    expect(error).toBeInstanceOf(FhirReadError);
    expect((error as FhirReadError).name).toBe("FhirReadError");
    expect((error as FhirReadError).httpStatus).toBe(403);
    expect((error as FhirReadError).message).toBe(
      `Healthcare API 403 Forbidden (response sha256 ${sha256(body)}, 2 issues)`,
    );
    expect((error as FhirReadError).message).not.toContain("tablets");
  });

  it("counts no issues in a refusal that is not an OperationOutcome", async () => {
    const body = { error: { code: 500, message: "backend error" } };
    answer = respond(500, body, "Internal Server Error");
    const reader = new HealthcareFhirReader(OPTIONS);

    await expect(reader.readBundleVersion("bundle-1", "1")).rejects.toThrow(
      `Healthcare API 500 Internal Server Error (response sha256 ${sha256(body)}, 0 issues)`,
    );
  });

  it("names the status of a refusal whose body is not JSON, and quotes nothing from it", async () => {
    // An HTML error page from a proxy in front of the store: before, response.json() threw a
    // bare SyntaxError and the status was lost.
    const page = "<html><body>502 Bad Gateway: take two tablets</body></html>";
    answer = () => new Response(page, { status: 502, statusText: "Bad Gateway" });
    const reader = new HealthcareFhirReader(OPTIONS);

    const error = await reader.readBundle("bundle-1").catch((e: unknown) => e);

    expect(error).toBeInstanceOf(FhirReadError);
    expect((error as FhirReadError).httpStatus).toBe(502);
    expect((error as FhirReadError).message).toBe(
      `Healthcare API 502 Bad Gateway (non-JSON response sha256 ${sha256Utf8(page)})`,
    );
    expect((error as FhirReadError).message).not.toContain("tablets");
  });

  it("releases the body of a missing resource without reading it", async () => {
    const answered: Response[] = [];
    answer = () => {
      const response = new Response(JSON.stringify({ resourceType: "OperationOutcome" }), {
        status: 404,
      });
      answered.push(response);
      return response;
    };
    const reader = new HealthcareFhirReader(OPTIONS);

    expect(await reader.readBundle("bundle-1")).toBeUndefined();
    expect(answered[0]?.bodyUsed).toBe(true);
  });

  it("bounds every read by a timeout, and cancels it with the request", async () => {
    answer = respond(200, DOCUMENT);
    const reader = new HealthcareFhirReader(OPTIONS);

    // No request signal: the read still carries its own timeout.
    await reader.readBundle("bundle-1");
    const alone = sent[0]?.init.signal;
    expect(alone).toBeInstanceOf(AbortSignal);
    expect(alone?.aborted).toBe(false);

    // With one: aborting the request aborts the read.
    const request = new AbortController();
    await reader.findProvenanceForBundle("bundle-1", request.signal);
    const joined = sent[1]?.init.signal;
    expect(joined?.aborted).toBe(false);
    request.abort();
    expect(joined?.aborted).toBe(true);
  });

  it("refuses when Application Default Credentials yield no token, before any request", async () => {
    auth.token = null;
    const reader = new HealthcareFhirReader(OPTIONS);

    const error = await reader.readBundle("bundle-1").catch((e: unknown) => e);
    expect(error).toBeInstanceOf(FhirReadError);
    expect((error as FhirReadError).message).toBe(
      "Application Default Credentials returned no token",
    );
    expect(sent).toEqual([]);
  });
});

describe("the Provenance search the reader sends", () => {
  it("asks the store for the newest first over a bounded page", async () => {
    answer = respond(200, {
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
        // A searchset may carry an OperationOutcome among its entries; it is never the answer.
        { fullUrl: "z", resource: { resourceType: "OperationOutcome", id: "0" } },
      ],
    });
    const reader = new HealthcareFhirReader(OPTIONS);

    const answered = await reader.findProvenanceForBundle("bundle-1");

    const url = new URL(only().url);
    expect(`${url.origin}${url.pathname}`).toBe(`${STORE}/Provenance`);
    expect(url.searchParams.get("target")).toBe("Bundle/bundle-1");
    expect(url.searchParams.get("_sort")).toBe("-recorded");
    expect(url.searchParams.get("_count")).toBe(String(PROVENANCE_PAGE_SIZE));
    // The store's order is asked for, and then not relied upon: the most recently written
    // approval is the answer.
    expect(answered?.id).toBe("written-last");
  });

  it("has no answer when the search result is not a Bundle with entries", async () => {
    answer = respond(200, { resourceType: "Bundle", type: "searchset", total: 0 });
    const reader = new HealthcareFhirReader(OPTIONS);
    expect(await reader.findProvenanceForBundle("bundle-1")).toBeUndefined();
  });
});
