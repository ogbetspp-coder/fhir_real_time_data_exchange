import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";

import { loadEmaMapping, type EmaMapping } from "../src/fhir/mapping.js";
import { transformType2ToEma } from "../src/fhir/transform.js";
import type { FhirResource } from "../src/fhir/types.js";
import { createSyntheticType2Bundle } from "../src/fixtures/synthetic.js";
import {
  HEALTHCARE_TIMEOUT_MS,
  HealthcareApiClient,
  HealthcareApiError,
  buildPersistTransaction,
} from "../src/gcp/healthcare.js";
import { sha256Utf8 } from "../src/lib/hash.js";

// The worker's Cloud Healthcare API client, over a stubbed `fetch` and a stubbed Application
// Default Credentials token: what it sends (URL, method, headers, body) and what it makes of what
// comes back, with no credential and no network. The credential is replaced at the boundary this
// module already has — `google-auth-library`'s GoogleAuth — the same way
// test/query/fhir-reader.test.ts replaces it for the query service's reader.

const auth = vi.hoisted(() => {
  const state: { token: string | null } = { token: "test-access-token" };
  return state;
});

vi.mock("google-auth-library", () => ({
  GoogleAuth: class {
    getAccessToken(): Promise<string | null> {
      return Promise.resolve(auth.token);
    }
  },
}));

const RUN_ID = "00000000-0000-4000-8000-0000000000aa";
const OPTIONS = {
  GOOGLE_CLOUD_PROJECT: "synthetic project",
  GCP_LOCATION: "europe-west4",
  HEALTHCARE_DATASET_ID: "ema/flow",
  SOURCE_FHIR_STORE_ID: "source",
  TARGET_FHIR_STORE_ID: "validated",
};
const BASE =
  "https://healthcare.googleapis.com/v1/projects/synthetic%20project/locations/europe-west4/datasets/ema%2Fflow/fhirStores";

type Sent = { url: string; init: RequestInit; headers: Headers };

let mapping: EmaMapping;
let sent: Sent[];
let answer: () => Response | Promise<Response>;

function respond(status: number, body: unknown): () => Response {
  return () =>
    new Response(JSON.stringify(body), {
      status,
      headers: { "content-type": "application/fhir+json" },
    });
}

beforeAll(async () => {
  mapping = await loadEmaMapping();
});

beforeEach(() => {
  auth.token = "test-access-token";
  sent = [];
  answer = respond(200, { resourceType: "OperationOutcome", issue: [] });
  vi.stubGlobal(
    "fetch",
    vi.fn((url: string, init: RequestInit = {}) => {
      sent.push({ url, init, headers: new Headers(init.headers) });
      return Promise.resolve().then(answer);
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

function expectStandardHeaders(headers: Headers): void {
  expect(headers.get("authorization")).toBe("Bearer test-access-token");
  expect(headers.get("content-type")).toBe("application/fhir+json; charset=utf-8");
  expect(headers.get("x-request-id")).toBe(RUN_ID);
  expect(headers.get("x-goog-healthcare-audit-appname")).toBe("ema-flow");
  expect(headers.get("x-goog-healthcare-audit-reason")).toBe("ePI interoperability pipeline");
}

describe("reading a source resource", () => {
  it("GETs the resource from the source store, every path segment encoded", async () => {
    answer = respond(200, { resourceType: "Bundle", id: "b/1" });
    const client = new HealthcareApiClient(OPTIONS);

    const resource = await client.readSourceResource("Bundle", "b/1", RUN_ID);

    expect(resource).toEqual({ resourceType: "Bundle", id: "b/1" });
    const request = only();
    expect(request.url).toBe(`${BASE}/source/fhir/Bundle/b%2F1`);
    expect(request.init.method).toBeUndefined();
    expectStandardHeaders(request.headers);
  });

  // Encoding leaves `.` and `..` alone and the URL parser resolves them: `Bundle/..` is the store.
  it.each([".", ".."])(
    "refuses the id %s, which names no resource, before any request",
    async (id) => {
      const client = new HealthcareApiClient(OPTIONS);

      await expect(client.readSourceResource("Bundle", id, RUN_ID)).rejects.toThrow(
        "A source resource id must be a single path segment",
      );
      expect(sent).toEqual([]);
    },
  );

  it("refuses without a source store, before any request", async () => {
    const client = new HealthcareApiClient({ ...OPTIONS, SOURCE_FHIR_STORE_ID: undefined });
    await expect(client.readSourceResource("Bundle", "b", RUN_ID)).rejects.toThrow(
      "SOURCE_FHIR_STORE_ID is required",
    );
    expect(sent).toEqual([]);
  });

  it.each(["GOOGLE_CLOUD_PROJECT", "HEALTHCARE_DATASET_ID"] as const)(
    "refuses without %s, before any request",
    async (name) => {
      const client = new HealthcareApiClient({ ...OPTIONS, [name]: undefined });
      await expect(client.readSourceResource("Bundle", "b", RUN_ID)).rejects.toThrow(
        "Healthcare API project and dataset configuration are required",
      );
      expect(sent).toEqual([]);
    },
  );

  it("refuses when Application Default Credentials yield no token, before any request", async () => {
    auth.token = null;
    const client = new HealthcareApiClient(OPTIONS);
    await expect(client.readSourceResource("Bundle", "b", RUN_ID)).rejects.toThrow(
      "Application Default Credentials returned no access token",
    );
    expect(sent).toEqual([]);
  });
});

describe("validating a resource", () => {
  it("POSTs the resource to $validate on the validated store with the profile as a query", async () => {
    const outcome = { resourceType: "OperationOutcome", issue: [{ severity: "information" }] };
    answer = respond(200, outcome);
    const client = new HealthcareApiClient(OPTIONS);
    const resource: FhirResource = { resourceType: "List", id: "l1" };
    const profile = "http://ema.europa.eu/fhir/StructureDefinition/EUEpiList";

    expect(await client.validate(resource, profile, RUN_ID)).toEqual(outcome);

    const request = only();
    const url = new URL(request.url);
    expect(`${url.origin}${url.pathname}`).toBe(`${BASE}/validated/fhir/List/$validate`);
    expect(url.searchParams.get("profile")).toBe(profile);
    expect([...url.searchParams.keys()]).toEqual(["profile"]);
    expect(request.init.method).toBe("POST");
    expect(JSON.parse(request.init.body as string)).toEqual(resource);
    expectStandardHeaders(request.headers);
  });

  it("refuses without a validated store, before any request", async () => {
    const client = new HealthcareApiClient({ ...OPTIONS, TARGET_FHIR_STORE_ID: undefined });
    await expect(client.validate({ resourceType: "List" }, "p", RUN_ID)).rejects.toThrow(
      "TARGET_FHIR_STORE_ID is required",
    );
    expect(sent).toEqual([]);
  });
});

// The transaction's precondition (docs/design/version-identity.md): the version of the document
// Bundle the validated store holds, read before the run signs.
describe("reading the stored version of a resource", () => {
  it("GETs it from the validated store and answers its version id", async () => {
    answer = respond(200, { resourceType: "Bundle", id: "b1", meta: { versionId: "MTc5" } });
    const client = new HealthcareApiClient(OPTIONS);

    expect(await client.readStoredVersion("Bundle", "b1", RUN_ID)).toEqual({ versionId: "MTc5" });

    const request = only();
    expect(request.url).toBe(`${BASE}/validated/fhir/Bundle/b1`);
    expect(request.init.method).toBe("GET");
    expectStandardHeaders(request.headers);
  });

  it("answers absent for a 404, and refuses a 410 and a resource without a version", async () => {
    answer = respond(404, { resourceType: "OperationOutcome", issue: [] });
    expect(await new HealthcareApiClient(OPTIONS).readStoredVersion("Bundle", "b", RUN_ID)).toBe(
      "absent",
    );

    answer = respond(410, { resourceType: "OperationOutcome", issue: [] });
    const gone = await new HealthcareApiClient(OPTIONS)
      .readStoredVersion("Bundle", "b", RUN_ID)
      .catch((e: unknown) => e);
    expect(gone).toBeInstanceOf(HealthcareApiError);
    expect([(gone as HealthcareApiError).operation, (gone as HealthcareApiError).status]).toEqual([
      "read-target",
      410,
    ]);

    answer = respond(200, { resourceType: "Bundle", id: "b" });
    await expect(
      new HealthcareApiClient(OPTIONS).readStoredVersion("Bundle", "b", RUN_ID),
    ).rejects.toThrow("The target store answered a resource without a version id");
  });

  it("refuses without a validated store, before any request", async () => {
    const client = new HealthcareApiClient({ ...OPTIONS, TARGET_FHIR_STORE_ID: undefined });
    await expect(client.readStoredVersion("Bundle", "b", RUN_ID)).rejects.toThrow(
      "TARGET_FHIR_STORE_ID is required",
    );
    expect(sent).toEqual([]);
  });
});

// The versioned approval link (docs/design/approval.md, D5) is created, never replaced: a PUT by
// its id with `If-None-Match: *`, and a refusal is the store's, by operation.
describe("creating a resource only if absent", () => {
  it("PUTs the resource by its id with If-None-Match: *", async () => {
    const link: FhirResource = { resourceType: "Provenance", id: "link-1" };
    answer = respond(201, link);
    expect(await new HealthcareApiClient(OPTIONS).createResource(link, RUN_ID)).toEqual(link);
    const request = only();
    expect(request.url).toBe(`${BASE}/validated/fhir/Provenance/link-1`);
    expect(request.init.method).toBe("PUT");
    expect(request.headers.get("if-none-match")).toBe("*");
    expectStandardHeaders(request.headers);
    expect(JSON.parse(request.init.body as string)).toEqual(link);
  });

  it("refuses when the store already holds one, naming the operation", async () => {
    answer = respond(412, { resourceType: "OperationOutcome", issue: [] });
    const refused = await new HealthcareApiClient(OPTIONS)
      .createResource({ resourceType: "Provenance", id: "link-1" }, RUN_ID)
      .catch((e: unknown) => e);
    expect(refused).toBeInstanceOf(HealthcareApiError);
    expect((refused as HealthcareApiError).operation).toBe("write-approval-link");
  });
});

function transaction(extras: FhirResource[] = []) {
  const target = transformType2ToEma(createSyntheticType2Bundle(mapping), mapping);
  return buildPersistTransaction(target.list, target.documentBundle, RUN_ID, "absent", extras);
}

describe("executing the transaction", () => {
  it("POSTs exactly the transaction it is given to the validated store's base", async () => {
    const extra: FhirResource = { resourceType: "Provenance", id: "prov-1" };
    const built = transaction([extra]);
    const response = { resourceType: "Bundle", type: "transaction-response", entry: [] };
    answer = respond(200, response);
    const client = new HealthcareApiClient(OPTIONS);

    expect(await client.executeTransaction(built, RUN_ID)).toEqual(response);

    const request = only();
    expect(request.url).toBe(`${BASE}/validated/fhir`);
    expect(request.init.method).toBe("POST");
    expectStandardHeaders(request.headers);
    expect(JSON.parse(request.init.body as string)).toEqual(built);
    expect(built.entry.at(-1)?.request).toEqual({ method: "PUT", url: "Provenance/prov-1" });
  });

  it("refuses without a validated store, before any request", async () => {
    const client = new HealthcareApiClient({ ...OPTIONS, TARGET_FHIR_STORE_ID: undefined });
    await expect(client.executeTransaction(transaction(), RUN_ID)).rejects.toThrow(
      "TARGET_FHIR_STORE_ID is required",
    );
    expect(sent).toEqual([]);
  });
});

describe("an answer that is not a FHIR response", () => {
  const HTML = "<html><body>502 Bad Gateway: Take two tablets</body></html>";

  // A Google front end answers an overload or a gateway fault with HTML. `response.json()` made
  // that a SyntaxError, `unclassified`, with the status and the operation lost.
  it.each([502, 503, 429])(
    "keeps the operation and the %i status of an HTML error page",
    async (status) => {
      answer = () => new Response(HTML, { status, headers: { "content-type": "text/html" } });
      const error = await new HealthcareApiClient(OPTIONS)
        .validate({ resourceType: "List" }, "p", RUN_ID)
        .catch((e: unknown) => e);

      expect(error).toBeInstanceOf(HealthcareApiError);
      const refusal = error as HealthcareApiError;
      expect([refusal.operation, refusal.status, refusal.codes]).toEqual(["validate", status, []]);
      expect(refusal.responseSha256).toBe(sha256Utf8(HTML));
      expect(refusal.message).not.toContain("tablets");
    },
  );

  it("refuses a success whose body is not a JSON object", async () => {
    answer = () => new Response(HTML, { status: 200 });
    await expect(
      new HealthcareApiClient(OPTIONS).validate({ resourceType: "List" }, "p", RUN_ID),
    ).rejects.toThrow("Healthcare API returned a response that is not a JSON object");
  });

  it("gives up on a call that does not answer in time", async () => {
    answer = () => Promise.reject(new DOMException("The operation timed out", "TimeoutError"));
    await expect(
      new HealthcareApiClient(OPTIONS).validate({ resourceType: "List" }, "p", RUN_ID),
    ).rejects.toThrow("Healthcare API request timed out");
    expect(only().init.signal).toBeInstanceOf(AbortSignal);
    expect(HEALTHCARE_TIMEOUT_MS).toBeLessThan(1_800_000);
  });

  it("lets any other transport failure through as it is", async () => {
    answer = () => Promise.reject(new TypeError("fetch failed"));
    await expect(
      new HealthcareApiClient(OPTIONS).validate({ resourceType: "List" }, "p", RUN_ID),
    ).rejects.toThrow("fetch failed");
  });
});

describe("a refusal from the Healthcare API", () => {
  const PROSE = "Patient narrative quoted back: Take two tablets";

  it("becomes a HealthcareApiError carrying the operation, status, machine codes and body hash", async () => {
    const body = {
      resourceType: "OperationOutcome",
      issue: [
        {
          severity: "error",
          code: "invalid",
          details: { text: "invalid_full_url" },
          diagnostics: PROSE,
        },
        // Repeated codes are carried once.
        { severity: "error", code: "invalid", details: { text: "invalid_full_url" } },
        // Prose in details.text is dropped: only a machine code travels.
        { severity: "error", code: "processing", details: { text: PROSE } },
        // A code that is not a string, and an issue that is not an object, carry nothing.
        { severity: "error", code: 42 },
        null,
      ],
    };
    answer = respond(422, body);
    const client = new HealthcareApiClient(OPTIONS);

    const error = await client
      .validate({ resourceType: "List" }, "p", RUN_ID)
      .catch((e: unknown) => e);

    expect(error).toBeInstanceOf(HealthcareApiError);
    const refusal = error as HealthcareApiError;
    expect(refusal.name).toBe("HealthcareApiError");
    expect(refusal.operation).toBe("validate");
    expect(refusal.status).toBe(422);
    expect(refusal.codes).toEqual(["invalid", "invalid_full_url", "processing"]);
    expect(refusal.responseSha256).toBe(sha256Utf8(JSON.stringify(body)));
    expect(refusal.message).toBe(
      `Healthcare API refused validate with 422 (response sha256 ${sha256Utf8(JSON.stringify(body))}, 3 codes)`,
    );
    // Nothing of the body's prose reaches the error.
    const { operation, status, codes, responseSha256, message } = refusal;
    expect(JSON.stringify({ operation, status, codes, responseSha256, message })).not.toContain(
      "tablets",
    );
  });

  it.each([
    [
      "read-source",
      (client: HealthcareApiClient) => client.readSourceResource("Bundle", "b", RUN_ID),
    ],
    [
      "validate",
      (client: HealthcareApiClient) => client.validate({ resourceType: "List" }, "p", RUN_ID),
    ],
    [
      "execute-bundle",
      (client: HealthcareApiClient) => client.executeTransaction(transaction(), RUN_ID),
    ],
  ] as const)("names %s as the refused operation", async (operation, call) => {
    answer = respond(403, { error: { code: 403, message: PROSE } });
    const error = await call(new HealthcareApiClient(OPTIONS)).catch((e: unknown) => e);

    expect(error).toBeInstanceOf(HealthcareApiError);
    expect((error as HealthcareApiError).operation).toBe(operation);
    expect((error as HealthcareApiError).status).toBe(403);
    // A body that is not an OperationOutcome carries no codes at all.
    expect((error as HealthcareApiError).codes).toEqual([]);
  });

  it("treats a 2xx OperationOutcome as the answer, not as a refusal", async () => {
    const outcome = {
      resourceType: "OperationOutcome",
      issue: [{ severity: "error", code: "invalid", details: { text: "profile_mismatch" } }],
    };
    answer = respond(200, outcome);
    const client = new HealthcareApiClient(OPTIONS);
    // $validate answers 200 with the issues it found; judging them is the pipeline's job.
    expect(await client.validate({ resourceType: "List" }, "p", RUN_ID)).toEqual(outcome);
  });
});
