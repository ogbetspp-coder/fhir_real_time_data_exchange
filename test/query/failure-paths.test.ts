import type { AddressInfo } from "node:net";
import type { Server } from "node:http";

import { afterEach, beforeAll, describe, expect, it, vi } from "vitest";

import { QueryAuditRecordSchema, type QueryAuditRecord } from "../../src/contracts/query-tools.js";
import { loadEmaMapping } from "../../src/fhir/mapping.js";
import type { FhirBundle } from "../../src/fhir/types.js";
import { sha256, stableUuid } from "../../src/lib/hash.js";
import { MAX_JSON_DEPTH } from "../../src/lib/json-shape.js";
import {
  MAX_BODY_BYTES,
  buildQueryServer,
  createQueryServer,
  type QueryAppDeps,
} from "../../src/query/app.js";
import type { CredentialVerifier } from "../../src/query/auth.js";
import { loadQueryConfig } from "../../src/query/config.js";
import { FhirReadError, type FhirReader } from "../../src/query/fhir-reader.js";
import { FIND_PRODUCT_CONCURRENCY, REQUEST_READ_CONCURRENCY } from "../../src/query/tools.js";
import {
  PRINCIPAL_A,
  SECTION_KEY,
  SERVICE_VERSION,
  buildQueryStore,
  callTool,
  connectHarness,
  createFakeReader,
  entitlementDirectory,
  testIdentity,
  type QueryStore,
  type SeededDocument,
} from "./fixtures.js";

// The paths on which a tool call does not simply succeed: a store read that throws, a stored
// document that cannot be cited, a request that runs out of reads, a body the service refuses,
// an audit record that cannot be written as built, and a request that ends while its reads are
// still in flight. On each, the caller gets a closed code and nothing else, the log gets a type
// and never a message, and the audit trail gets exactly one record per call.

const AUDIENCE = "https://query.ema-flow.invalid";

// Stands in for credential verification exactly as http.test.ts does: `aud=<audience>;sub=<s>`.
const verifier: CredentialVerifier = {
  verify(token: string) {
    const match = /^aud=([^;]+);sub=([A-Za-z0-9]+)$/.exec(token);
    if (match?.[1] !== AUDIENCE || match[2] === undefined) {
      return Promise.resolve({ rejected: "id-token-rejected" as const });
    }
    return Promise.resolve({ principal: match[2], credentialType: "id-token" as const });
  },
};
const AUTHORIZATION = `Bearer aud=${AUDIENCE};sub=${PRINCIPAL_A}`;

// A message an upstream error could carry — the kind of text a FHIR error body quotes. It must
// reach neither the caller nor the log.
const CANARY = "CANARY take two tablets 7f3e";

let store: QueryStore;

beforeAll(async () => {
  store = buildQueryStore(await loadEmaMapping());
});

const opened: Server[] = [];

afterEach(async () => {
  for (const server of opened.splice(0)) {
    server.closeAllConnections();
    await new Promise<void>((resolve) => {
      server.close(() => {
        resolve();
      });
    });
  }
});

async function listen(server: Server): Promise<string> {
  opened.push(server);
  await new Promise<void>((resolve) => {
    server.listen(0, "127.0.0.1", resolve);
  });
  return `http://127.0.0.1:${String((server.address() as AddressInfo).port)}`;
}

async function serve(
  overrides: Partial<QueryAppDeps> & { audits?: QueryAuditRecord[] },
): Promise<string> {
  const { audits, ...rest } = overrides;
  return listen(
    createQueryServer({
      reader: createFakeReader(store.documents).reader,
      mapping: store.mapping,
      serviceVersion: SERVICE_VERSION,
      verifier,
      entitlements: entitlementDirectory(store),
      audit: (record) => audits?.push(record),
      ...rest,
    }),
  );
}

function post(origin: string, body: string, authorization = AUTHORIZATION): Promise<Response> {
  return fetch(`${origin}/mcp`, {
    method: "POST",
    headers: {
      "content-type": "application/json",
      accept: "application/json, text/event-stream",
      authorization,
    },
    body,
  });
}

function callBody(name: string, args: unknown, id: number | string = 1) {
  return { jsonrpc: "2.0", id, method: "tools/call", params: { name, arguments: args } };
}

type RpcAnswer = {
  id?: unknown;
  result?: { isError?: boolean; structuredContent?: unknown; content?: unknown };
  error?: unknown;
};

async function captured<T>(run: () => Promise<T>): Promise<{ value: T; lines: string[] }> {
  const lines: string[] = [];
  const push = (...args: unknown[]): void => {
    for (const argument of args) {
      lines.push(typeof argument === "string" ? argument : JSON.stringify(argument));
    }
  };
  const outSpy = vi.spyOn(console, "log").mockImplementation(push);
  const errSpy = vi.spyOn(console, "error").mockImplementation(push);
  try {
    return { value: await run(), lines };
  } finally {
    outSpy.mockRestore();
    errSpy.mockRestore();
  }
}

function parsedLines(lines: string[]): Record<string, unknown>[] {
  return lines.map((line) => JSON.parse(line) as Record<string, unknown>);
}

function rejecting(error: () => Error): FhirReader {
  return {
    readBundle: () => Promise.reject(error()),
    readBundleVersion: () => Promise.reject(error()),
    findProvenanceForBundle: () => Promise.reject(error()),
  };
}

describe("a tool whose store read throws", () => {
  it.each([
    ["a plain error", () => new Error(CANARY)],
    ["a store refusal", () => new FhirReadError(CANARY, 502)],
  ])(
    "answers unavailable to every tool, leaks nothing, and audits each call once (%s)",
    async (_, error) => {
      const audits: QueryAuditRecord[] = [];
      const origin = await serve({ reader: rejecting(error), audits });
      const document = { bundleId: store.bundleIdA };
      const batch = [
        callBody("find_product", { query: "synthetic" }, 1),
        callBody("get_section", { ...document, sourceKey: SECTION_KEY }, 2),
        callBody("get_provenance", document, 3),
        callBody("verify_quote", { ...document, quote: "tablets" }, 4),
      ];

      const { value: text, lines } = await captured(async () => {
        const response = await post(origin, JSON.stringify(batch));
        expect(response.status).toBe(200);
        return response.text();
      });

      expect(text).not.toContain("CANARY");
      const answers = JSON.parse(text) as RpcAnswer[];
      expect(answers).toHaveLength(4);
      for (const answer of answers) {
        expect(answer.error).toBeUndefined();
        expect(answer.result).toEqual({
          isError: true,
          content: [{ type: "text", text: "unavailable" }],
        });
      }
      expect(audits.map(({ tool, outcome }) => [tool, outcome]).sort()).toEqual(
        [
          ["find_product", "unavailable"],
          ["get_provenance", "unavailable"],
          ["get_section", "unavailable"],
          ["verify_quote", "unavailable"],
        ].sort(),
      );

      expect(lines.join("\n")).not.toContain("CANARY");
      const failures = parsedLines(lines).filter((line) => line.message === "Query tool failed");
      expect(failures).toHaveLength(4);
      for (const line of failures) {
        expect(line.errorType).toBe(error().name);
        // A store refusal names its HTTP status, so an HTML 502 from a proxy is diagnosable.
        expect(line.httpStatus).toBe(error() instanceof FhirReadError ? 502 : undefined);
      }
    },
  );
});

describe("find_product when one read fails", () => {
  it("launches no further read, and none is in flight once it answers", async () => {
    const seeded = store.documents.get(store.bundleIdA);
    if (seeded === undefined) throw new Error("expected a seeded document");
    const ids = Array.from({ length: 200 }, (_, position) =>
      stableUuid("ema-bundle", `stop-${String(position)}`),
    );
    let started = 0;
    let settled = 0;
    const reader: FhirReader = {
      readBundle: async () => {
        started += 1;
        const failing = started === 3;
        await new Promise((resolve) => setTimeout(resolve, 5));
        settled += 1;
        if (failing) throw new Error(CANARY);
        return structuredClone(seeded.bundle);
      },
      readBundleVersion: () => Promise.reject(new Error("unused")),
      findProvenanceForBundle: () => Promise.reject(new Error("unused")),
    };
    const harness = await connectHarness({
      store,
      principal: PRINCIPAL_A,
      entitlements: { bundles: ids },
      documents: new Map<string, SeededDocument>(),
      wrapReader: () => reader,
    });
    try {
      const answer = await callTool(harness, "find_product", { query: "no-such-product" });
      expect(answer).toMatchObject({ isError: true, text: "unavailable" });
      // Every read the call started had finished before it answered.
      expect(settled).toBe(started);
      // One pool's worth at most after the failure, never the rest of the entitlement.
      expect(started).toBeLessThanOrEqual(2 + FIND_PRODUCT_CONCURRENCY);
      const atAnswer = started;
      await new Promise((resolve) => setTimeout(resolve, 50));
      expect(started).toBe(atAnswer);
      expect(harness.audits.map(({ outcome }) => outcome)).toEqual(["unavailable"]);
    } finally {
      await harness.close();
    }
  });
});

describe("store reads in flight", () => {
  it("are bounded per request, across every call of a batch", async () => {
    const seeded = store.documents.get(store.bundleIdA);
    if (seeded === undefined) throw new Error("expected a seeded document");
    const ids = Array.from({ length: 40 }, (_, position) =>
      stableUuid("ema-bundle", `pool-${String(position)}`),
    );
    let inFlight = 0;
    let most = 0;
    const reader: FhirReader = {
      readBundle: async () => {
        inFlight += 1;
        most = Math.max(most, inFlight);
        await new Promise((resolve) => setTimeout(resolve, 2));
        inFlight -= 1;
        return structuredClone(seeded.bundle);
      },
      readBundleVersion: () => Promise.reject(new Error("unused")),
      findProvenanceForBundle: () => Promise.reject(new Error("unused")),
    };
    const origin = await serve({
      reader,
      entitlements: { entitlementsFor: () => ({ bundles: ids }) },
    });

    // Eight find_product calls, dispatched concurrently by the SDK, each with a pool of eight.
    const response = await post(
      origin,
      JSON.stringify(
        Array.from({ length: 8 }, (_, position) =>
          callBody("find_product", { query: "no-such-product" }, position + 1),
        ),
      ),
    );
    expect(response.status).toBe(200);
    const answers = (await response.json()) as RpcAnswer[];
    expect(answers.every((answer) => answer.result?.isError !== true)).toBe(true);
    expect(most).toBeGreaterThan(1);
    expect(most).toBeLessThanOrEqual(REQUEST_READ_CONCURRENCY);
  });

  it("are cancelled when the request ends at its deadline", async () => {
    const signals: (AbortSignal | undefined)[] = [];
    let reads = 0;
    const hang = (signal?: AbortSignal): Promise<never> => {
      reads += 1;
      signals.push(signal);
      return new Promise<never>((_, reject) => {
        signal?.addEventListener("abort", () => {
          reject(signal.reason as Error);
        });
      });
    };
    const reader: FhirReader = {
      readBundle: (_, signal) => hang(signal),
      readBundleVersion: (_, __, signal) => hang(signal),
      findProvenanceForBundle: (_, signal) => hang(signal),
    };
    const audits: QueryAuditRecord[] = [];
    const origin = await serve({ reader, audits, requestDeadlineMs: 100 });

    const response = await post(
      origin,
      JSON.stringify(callBody("find_product", { query: "synthetic" })),
    );
    expect(response.status).toBe(503);
    expect(await response.json()).toEqual({ error: "unavailable" });

    expect(signals.length).toBeGreaterThan(0);
    for (const signal of signals) expect(signal?.aborted).toBe(true);
    // Nothing more is read for the abandoned call, and it is recorded once.
    const readsAtAnswer = reads;
    await new Promise((resolve) => setTimeout(resolve, 50));
    expect(reads).toBe(readsAtAnswer);
    expect(audits.map(({ outcome }) => outcome)).toEqual(["unavailable"]);
  });
});

describe("a stored document that cannot be answered from", () => {
  it.each([
    [
      "a Bundle that does not say its version",
      (bundle: FhirBundle): FhirBundle => ({ ...bundle, meta: {} }),
    ],
    [
      "a Bundle whose first entry is not a Composition",
      (bundle: FhirBundle): FhirBundle => ({ ...bundle, entry: bundle.entry.slice(1) }),
    ],
  ])("is unavailable: %s", async (_, change) => {
    const seeded = store.documents.get(store.bundleIdA);
    if (seeded === undefined) throw new Error("expected a seeded document");
    const harness = await connectHarness({
      store,
      principal: PRINCIPAL_A,
      entitlements: { bundles: [store.bundleIdA] },
      documents: new Map([[store.bundleIdA, { ...seeded, bundle: change(seeded.bundle) }]]),
    });
    try {
      for (const [tool, args] of [
        ["get_section", { bundleId: store.bundleIdA, sourceKey: SECTION_KEY }],
        ["get_provenance", { bundleId: store.bundleIdA }],
        ["verify_quote", { bundleId: store.bundleIdA, quote: "tablets" }],
      ] as const) {
        const answer = await callTool(harness, tool, args);
        expect([tool, answer.isError, answer.text]).toEqual([tool, true, "unavailable"]);
      }
      expect(harness.audits.map(({ outcome }) => outcome)).toEqual([
        "unavailable",
        "unavailable",
        "unavailable",
      ]);
    } finally {
      await harness.close();
    }
  });

  it("is unavailable when the budget runs out on the version-standing read", async () => {
    // One read pays for the named version; finding out whether it is the current one would
    // take a second.
    for (const tool of ["get_section", "get_provenance"] as const) {
      const harness = await connectHarness({
        store,
        principal: PRINCIPAL_A,
        entitlements: { bundles: [store.bundleIdA] },
        readBudget: 1,
      });
      try {
        const answer = await callTool(harness, tool, {
          bundleId: store.bundleIdA,
          versionId: "1",
          ...(tool === "get_section" ? { sourceKey: SECTION_KEY } : {}),
        });
        expect([tool, answer.isError, answer.text]).toEqual([tool, true, "unavailable"]);
        expect(harness.log.bundles).toEqual([`${store.bundleIdA}/_history/1`]);
        expect(harness.log.provenance).toEqual([]);
        expect(harness.audits.map(({ outcome }) => outcome)).toEqual(["unavailable"]);
      } finally {
        await harness.close();
      }
    }
  });
});

describe("a validating MCP client", () => {
  it("receives a tool error as a tool error after listTools, not as its own validation error", async () => {
    const harness = await connectHarness({
      store,
      principal: PRINCIPAL_A,
      entitlements: { bundles: [store.bundleIdA] },
    });
    try {
      await harness.client.listTools();
      // Without `structuredContent` there is nothing for the client to hold to the success
      // schema; with it, SDK 1.30.0 threw McpError -32602 here instead of returning.
      const result = await harness.client.callTool({
        name: "get_section",
        arguments: { bundleId: store.bundleIdA, sourceKey: "smpc.99" },
      });
      expect(result).toEqual({
        isError: true,
        content: [{ type: "text", text: "section-not-found" }],
      });
    } finally {
      await harness.close();
    }
  });
});

describe("a body the service refuses before the transport", () => {
  it("refuses arguments nested past the bound, naming the reason", async () => {
    const audits: QueryAuditRecord[] = [];
    const origin = await serve({ audits });
    // About six kilobytes: enough to reach a recursive hash's call-stack limit.
    const deep = `${"[".repeat(3_000)}${"]".repeat(3_000)}`;
    const body = `{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"find_product","arguments":{"query":${deep}}}}`;

    const { value: response, lines } = await captured(() => post(origin, body));
    expect(response.status).toBe(400);
    expect(await response.json()).toEqual({ error: "invalid-request" });
    // Refused before dispatch: nothing ran, so there is no call to record, and the warning line
    // is the trace, with a closed reason.
    expect(audits).toEqual([]);
    const refusals = parsedLines(lines).filter((line) => line.event === "refused-body");
    expect(refusals).toHaveLength(1);
    expect(refusals[0]).toMatchObject({ reason: "too-complex", principal: PRINCIPAL_A });

    // Nesting at the bound is still answered: the bound is a depth no MCP message needs, not
    // one it could meet. In a batch the argument's value sits at depth 4 (batch, message,
    // params, arguments), so this one's innermost value is at exactly MAX_JSON_DEPTH: a tool
    // error, not a refused body. One level more is refused.
    let atBound: unknown = "x";
    for (let depth = 4; depth < MAX_JSON_DEPTH; depth += 1) atBound = [atBound];
    const answered = await post(
      origin,
      JSON.stringify([callBody("find_product", { query: atBound })]),
    );
    expect(answered.status).toBe(200);
    // The SDK answers a one-entry batch with the entry itself.
    const entry = (await answered.json()) as RpcAnswer;
    expect(entry.result).toEqual({
      isError: true,
      content: [{ type: "text", text: "invalid-request" }],
    });
    const past = await post(
      origin,
      JSON.stringify([callBody("find_product", { query: [atBound] })]),
    );
    expect(past.status).toBe(400);
    await past.body?.cancel();
  });

  it("refuses a body over the size cap, naming the reason", async () => {
    const audits: QueryAuditRecord[] = [];
    const origin = await serve({ audits });
    const padding = "x".repeat(MAX_BODY_BYTES);
    const body = JSON.stringify(callBody("find_product", { query: padding }));

    const { value: status, lines } = await captured(async () => {
      const response = await post(origin, body);
      const answer: unknown = await response.json();
      expect(answer).toEqual({ error: "invalid-request" });
      return response.status;
    });
    expect(status).toBe(400);
    expect(audits).toEqual([]);
    const refusals = parsedLines(lines).filter((line) => line.event === "refused-body");
    expect(refusals.map(({ reason }) => reason)).toEqual(["too-large"]);
  });
});

describe("an audit record", () => {
  it("is written for arguments nested too deep to hash recursively", async () => {
    // The HTTP layer refuses such a body; a transport that does not (this in-memory one) still
    // gets exactly one record, whose hash is of the arguments themselves.
    const harness = await connectHarness({
      store,
      principal: PRINCIPAL_A,
      entitlements: { bundles: [store.bundleIdA] },
    });
    let nested: unknown = "leaf";
    for (let depth = 0; depth < 20_000; depth += 1) nested = [nested];
    const args = { query: nested };
    try {
      const answer = await callTool(harness, "find_product", args);
      expect(answer).toMatchObject({ isError: true, text: "invalid-request" });
      const [record] = harness.audits;
      expect(harness.audits).toHaveLength(1);
      expect(record).toMatchObject({ outcome: "invalid-request", argumentsSha256: sha256(args) });
      expect(record?.degraded).toBeUndefined();
    } finally {
      await harness.close();
    }
  });

  it("falls back to its required fields, saying why, rather than being lost", async () => {
    // A turn id the contract refuses makes the full record invalid: the fallback is written.
    const rejected = await connectHarness({
      store,
      principal: PRINCIPAL_A,
      entitlements: { bundles: [store.bundleIdA] },
      identity: testIdentity(PRINCIPAL_A, { turnId: "not-a-uuid" }),
    });
    try {
      await callTool(rejected, "get_section", {
        bundleId: store.bundleIdA,
        sourceKey: SECTION_KEY,
      });
      expect(rejected.audits).toHaveLength(1);
      const record = QueryAuditRecordSchema.parse(rejected.audits[0]);
      expect(record).toMatchObject({ outcome: "ok", degraded: "record-rejected" });
      expect(record.turnId).toBeUndefined();
      expect(record.bundleId).toBeUndefined();
    } finally {
      await rejected.close();
    }

    // Arguments that cannot be hashed at all (a BigInt has no JSON form) are recorded with the
    // hash of JSON null, marked as not describing them.
    const unhashable = await connectHarness({
      store,
      principal: PRINCIPAL_A,
      entitlements: { bundles: [store.bundleIdA] },
    });
    try {
      const answer = await callTool(unhashable, "find_product", { query: 10n });
      expect(answer).toMatchObject({ isError: true, text: "invalid-request" });
      expect(unhashable.audits).toHaveLength(1);
      expect(QueryAuditRecordSchema.parse(unhashable.audits[0])).toMatchObject({
        outcome: "invalid-request",
        argumentsSha256: sha256(null),
        degraded: "arguments-unhashable",
      });
    } finally {
      await unhashable.close();
    }
  });

  it("that cannot be written does not turn the answer into an internal error", async () => {
    const origin = await serve({
      audit: () => {
        throw new Error(CANARY);
      },
    });
    const { value: text, lines } = await captured(async () => {
      const response = await post(
        origin,
        JSON.stringify(callBody("get_section", { bundleId: 7, sourceKey: SECTION_KEY })),
      );
      expect(response.status).toBe(200);
      return response.text();
    });
    expect(text).not.toContain("CANARY");
    expect((JSON.parse(text) as RpcAnswer).result).toEqual({
      isError: true,
      content: [{ type: "text", text: "invalid-request" }],
    });
    expect(lines.join("\n")).not.toContain("CANARY");
    expect(
      parsedLines(lines).some((line) => line.message === "Query audit record could not be written"),
    ).toBe(true);
  });
});

describe("the service as its configuration builds it", () => {
  const REQUIRED = {
    GOOGLE_CLOUD_PROJECT: "synthetic-project",
    HEALTHCARE_DATASET_ID: "ema-flow",
    TARGET_FHIR_STORE_ID: "validated",
    QUERY_AUDIENCE: AUDIENCE,
  };

  async function refusals(
    flag: string | undefined,
    authorizations: (string | undefined)[],
  ): Promise<{ lines: string[]; warnings: Record<string, unknown>[] }> {
    const config = loadQueryConfig({
      ...REQUIRED,
      ...(flag === undefined ? {} : { QUERY_LOG_REJECTION_REASON: flag }),
    });
    const origin = await listen(buildQueryServer(config, store.mapping));
    const { lines } = await captured(async () => {
      for (const authorization of authorizations) {
        const response = await fetch(`${origin}/mcp`, {
          method: "POST",
          headers: {
            "content-type": "application/json",
            accept: "application/json, text/event-stream",
            ...(authorization === undefined ? {} : { authorization }),
          },
          body: JSON.stringify(callBody("find_product", { query: "synthetic" })),
        });
        expect(response.status).toBe(401);
        expect(await response.json()).toEqual({ error: "unauthenticated" });
      }
    });
    return {
      lines,
      warnings: parsedLines(lines).filter((line) => line.event === "unauthenticated"),
    };
  }

  // Neither refusal below reaches the network: no Bearer at all, and an opaque token on a
  // service that accepts no access tokens (QUERY_OAUTH_CLIENT_IDS unset).
  const SECRET = "opaque-secret-access-token-value";

  it("logs the category of a refused credential when QUERY_LOG_REJECTION_REASON is set", async () => {
    const { lines, warnings } = await refusals("true", [undefined, `Bearer ${SECRET}`]);
    expect(warnings.map(({ reason }) => reason)).toEqual([
      "no-bearer",
      "access-tokens-not-accepted",
    ]);
    // The category, never the token, its hash, or the scheme.
    const logged = lines.join("\n");
    for (const forbidden of [SECRET, "Bearer", sha256(SECRET)]) {
      expect([forbidden, logged.includes(forbidden)]).toEqual([forbidden, false]);
    }
  });

  it("logs no category when the flag is unset", async () => {
    const { warnings } = await refusals(undefined, [undefined, `Bearer ${SECRET}`]);
    expect(warnings).toHaveLength(2);
    for (const line of warnings) expect("reason" in line).toBe(false);
  });
});
