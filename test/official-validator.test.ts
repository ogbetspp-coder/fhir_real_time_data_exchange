import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  OFFICIAL_VALIDATOR_TIMEOUT_MS,
  OfficialFhirValidatorClient,
  OfficialValidatorError,
} from "../src/fhir/official-validator.js";
import { sha256Utf8 } from "../src/lib/hash.js";

// The official HL7 validator sidecar's client over a stubbed `fetch`: what it asks, and what it
// makes of an answer that is not an OperationOutcome.

type Sent = { url: string; init: RequestInit };

let sent: Sent[];
let answer: () => Promise<Response>;

beforeEach(() => {
  sent = [];
  answer = () =>
    Promise.resolve(new Response(JSON.stringify({ resourceType: "OperationOutcome", issue: [] })));
  vi.stubGlobal(
    "fetch",
    vi.fn((url: URL, init: RequestInit = {}) => {
      sent.push({ url: url.toString(), init });
      return answer();
    }),
  );
});

afterEach(() => {
  vi.unstubAllGlobals();
});

const client = (): OfficialFhirValidatorClient =>
  new OfficialFhirValidatorClient("http://localhost:8090");

describe("the official validator client", () => {
  it("POSTs the resource with its profiles and a bounded wait, and returns the outcome", async () => {
    const outcome = await client().validate({ resourceType: "List", id: "l" }, ["http://p/a"]);

    expect(outcome).toEqual({ resourceType: "OperationOutcome", issue: [] });
    const [request] = sent;
    const url = new URL(request?.url ?? "");
    expect(url.pathname).toBe("/validateResource");
    expect(url.searchParams.getAll("profile")).toEqual(["http://p/a"]);
    expect(request?.init.method).toBe("POST");
    expect(request?.init.signal).toBeInstanceOf(AbortSignal);
    expect(OFFICIAL_VALIDATOR_TIMEOUT_MS).toBeLessThan(1_800_000);
  });

  // An HTML error page used to become a SyntaxError from `response.json()`, losing the status.
  it("keeps the status of a refusal and carries its body only by hash", async () => {
    const page = "<html>500: Take two tablets</html>";
    answer = () => Promise.resolve(new Response(page, { status: 500 }));

    const error = await client()
      .validate({ resourceType: "List" })
      .catch((e: unknown) => e);

    expect(error).toBeInstanceOf(OfficialValidatorError);
    expect((error as OfficialValidatorError).status).toBe(500);
    expect((error as OfficialValidatorError).responseSha256).toBe(sha256Utf8(page));
    expect((error as Error).message).not.toContain("tablets");
  });

  it.each([
    ["not JSON", "<html>ok</html>"],
    ["JSON that is not an OperationOutcome", JSON.stringify({ resourceType: "Bundle" })],
    ["JSON null", "null"],
  ])("refuses a success whose body is %s", async (_name, body) => {
    answer = () => Promise.resolve(new Response(body, { status: 200 }));
    await expect(client().validate({ resourceType: "List" })).rejects.toThrow(
      "Official FHIR validator returned an invalid OperationOutcome",
    );
  });

  it("gives up on a validation that does not answer in time", async () => {
    answer = () => Promise.reject(new DOMException("timed out", "TimeoutError"));
    await expect(client().validate({ resourceType: "List" })).rejects.toThrow(
      "Official FHIR validator request timed out",
    );
  });

  it("lets any other transport failure through as it is", async () => {
    answer = () => Promise.reject(new TypeError("fetch failed"));
    await expect(client().validate({ resourceType: "List" })).rejects.toThrow("fetch failed");
  });
});
