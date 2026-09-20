import type { AddressInfo } from "node:net";
import type { Server } from "node:http";

import { afterAll, beforeAll, describe, expect, it, vi } from "vitest";

import { loadEmaMapping } from "../../src/fhir/mapping.js";
import type { QueryAuditRecord } from "../../src/contracts/query-tools.js";
import { sha256Utf8 } from "../../src/lib/hash.js";
import { MAX_BATCH_MESSAGES, TURN_ID_HEADER, createQueryServer } from "../../src/query/app.js";
import { bearerToken, type CredentialVerifier } from "../../src/query/auth.js";
import type { FhirReader } from "../../src/query/fhir-reader.js";
import {
  PRINCIPAL_A,
  PRINCIPAL_UNKNOWN,
  SECTION_KEY,
  SERVICE_VERSION,
  VERSION_ID,
  buildQueryStore,
  createFakeReader,
  entitlementDirectory,
  type QueryStore,
  type ReadLog,
} from "./fixtures.js";

// One real HTTP round trip on an ephemeral port, with an injected verifier: the Bearer check,
// the entitlement check, the request-shape limits, the health endpoint, and the fact that
// nothing about a token ever reaches a log line.

const AUDIENCE = "https://query.ema-flow.invalid";
const IMAGE_DIGEST = `sha256:${"ab".repeat(32)}`;
const TURN_ID = "0f6d1a2e-3b4c-4d5e-8f60-718293a4b5c6";

// Stands in for credential verification: same contract, same audience rule, no network. A
// token of the form `aud=<audience>;sub=<subject>` is an ID token; `access:<subject>` stands in
// for an opaque access token the real verifier would have checked with Google.
const verifier: CredentialVerifier = {
  verify(token: string) {
    const access = /^access:([A-Za-z0-9]+)$/.exec(token);
    if (access?.[1] !== undefined) {
      return Promise.resolve({ principal: access[1], credentialType: "access-token" as const });
    }
    const match = /^aud=([^;]+);sub=([A-Za-z0-9]+)$/.exec(token);
    if (match?.[1] !== AUDIENCE || match[2] === undefined) return Promise.resolve(undefined);
    return Promise.resolve({ principal: match[2], credentialType: "id-token" as const });
  },
};

function token(subject: string, audience = AUDIENCE): string {
  return `aud=${audience};sub=${subject}`;
}

let store: QueryStore;
let server: Server;
let origin: string;
let reads: ReadLog;
const audits: QueryAuditRecord[] = [];

beforeAll(async () => {
  store = buildQueryStore(await loadEmaMapping());
  const fake = createFakeReader(store.documents);
  reads = fake.log;
  server = createQueryServer({
    reader: fake.reader,
    mapping: store.mapping,
    serviceVersion: SERVICE_VERSION,
    imageDigest: IMAGE_DIGEST,
    verifier,
    entitlements: entitlementDirectory(store),
    audit: (record) => audits.push(record),
  });
  await new Promise<void>((resolve) => {
    server.listen(0, "127.0.0.1", resolve);
  });
  const address = server.address() as AddressInfo;
  origin = `http://127.0.0.1:${String(address.port)}`;
});

afterAll(async () => {
  server.closeAllConnections();
  await new Promise<void>((resolve) => {
    server.close(() => {
      resolve();
    });
  });
});

async function post(
  body: unknown,
  authorization?: string,
  headers: Record<string, string> = {},
): Promise<Response> {
  return fetch(`${origin}/mcp`, {
    method: "POST",
    headers: {
      "content-type": "application/json",
      accept: "application/json, text/event-stream",
      ...(authorization === undefined ? {} : { authorization }),
      ...headers,
    },
    body: JSON.stringify(body),
  });
}

function callBody(name: string, args: Record<string, unknown>, id: number | string = 1): unknown {
  return { jsonrpc: "2.0", id, method: "tools/call", params: { name, arguments: args } };
}

// A tools/call notification: no id, so the protocol never answers it and the transport drops it.
function callNotification(name: string, args: Record<string, unknown>): unknown {
  return { jsonrpc: "2.0", method: "tools/call", params: { name, arguments: args } };
}

// The JSON-RPC notification a client sends to cancel an in-flight request.
function cancelNotification(requestId: number | string): unknown {
  return {
    jsonrpc: "2.0",
    method: "notifications/cancelled",
    params: { requestId, reason: "client changed its mind" },
  };
}

// Structured log lines written while `run` executes, parsed back; the spies are restored after.
async function capturedLogLines(run: () => Promise<void>): Promise<Record<string, unknown>[]> {
  const raw: string[] = [];
  const push = (...args: unknown[]): void => {
    for (const argument of args)
      raw.push(typeof argument === "string" ? argument : JSON.stringify(argument));
  };
  const outSpy = vi.spyOn(console, "log").mockImplementation(push);
  const errSpy = vi.spyOn(console, "error").mockImplementation(push);
  try {
    await run();
  } finally {
    outSpy.mockRestore();
    errSpy.mockRestore();
  }
  return raw.map((line) => JSON.parse(line) as Record<string, unknown>);
}

describe("query service HTTP surface", () => {
  it("answers /healthz without a token", async () => {
    const response = await fetch(`${origin}/healthz`);

    expect(response.status).toBe(200);
    expect(await response.json()).toEqual({
      status: "ok",
      service: "ema-flow-query",
      version: SERVICE_VERSION,
    });
  });

  it("rejects a request with no Authorization header", async () => {
    const response = await post(callBody("get_section", { bundleId: store.bundleIdA }));

    expect(response.status).toBe(401);
    // The body is the whole answer: no hint about the tool, the store, or the token.
    expect(await response.json()).toEqual({ error: "unauthenticated" });
  });

  it("rejects a token issued for another audience", async () => {
    const response = await post(
      callBody("get_section", { bundleId: store.bundleIdA, sourceKey: SECTION_KEY }),
      `Bearer ${token(PRINCIPAL_A, "https://somewhere-else.invalid")}`,
    );

    expect(response.status).toBe(401);
    expect(await response.json()).toEqual({ error: "unauthenticated" });
  });

  it("rejects a token the verifier does not accept", async () => {
    const response = await post(
      callBody("find_product", { query: "synthetic" }),
      "Bearer not-a-token",
    );

    expect(response.status).toBe(401);
    expect(await response.json()).toEqual({ error: "unauthenticated" });
  });

  it("logs a rejected authentication as a structured warning with nothing from the token", async () => {
    const lines = await capturedLogLines(async () => {
      const response = await post(
        callBody("find_product", { query: "synthetic" }),
        `Bearer ${token(PRINCIPAL_A, "https://somewhere-else.invalid")}`,
      );
      expect(response.status).toBe(401);
    });

    const warnings = lines.filter((line) => line.event === "unauthenticated");
    expect(warnings).toHaveLength(1);
    const [line] = warnings;
    expect(line).toMatchObject({
      severity: "WARNING",
      service: "ema-flow-query",
      stage: "query-http",
      event: "unauthenticated",
    });
    // No principal, no reason, no audience: the line says only that a request was refused.
    expect(Object.keys(line ?? {}).sort()).toEqual(
      ["event", "message", "service", "severity", "stage", "timestamp"].sort(),
    );
  });

  it("refuses an authenticated principal with no entitlement before the protocol", async () => {
    const before = audits.length;
    const lines = await capturedLogLines(async () => {
      const call = await post(
        callBody("get_section", { bundleId: store.bundleIdA, sourceKey: SECTION_KEY }),
        `Bearer ${token(PRINCIPAL_UNKNOWN)}`,
      );
      expect(call.status).toBe(403);
      expect(await call.json()).toEqual({ error: "not-entitled" });

      // tools/list is part of the protocol, and the protocol is never reached.
      const list = await post(
        { jsonrpc: "2.0", id: 1, method: "tools/list", params: {} },
        `Bearer ${token(PRINCIPAL_UNKNOWN)}`,
      );
      expect(list.status).toBe(403);
      expect(await list.json()).toEqual({ error: "not-entitled" });
    });

    expect(audits.length).toBe(before);
    const refusals = lines.filter((line) => line.event === "not-entitled");
    expect(refusals).toHaveLength(2);
    for (const line of refusals) {
      // The principal is on the line: it is the opaque subject an operator would entitle.
      expect(line).toMatchObject({
        severity: "WARNING",
        service: "ema-flow-query",
        stage: "query-http",
        event: "not-entitled",
        principal: PRINCIPAL_UNKNOWN,
      });
    }
  });

  it("does not open a server-to-client stream", async () => {
    const response = await fetch(`${origin}/mcp`, {
      method: "GET",
      headers: { accept: "text/event-stream", authorization: `Bearer ${token(PRINCIPAL_A)}` },
    });

    expect(response.status).toBe(405);
    await response.body?.cancel();
  });

  it("answers a tool call over the streamable HTTP transport", async () => {
    const before = audits.length;
    const response = await post(
      callBody("get_section", { bundleId: store.bundleIdA, sourceKey: SECTION_KEY }),
      `Bearer ${token(PRINCIPAL_A)}`,
    );

    expect(response.status).toBe(200);
    const body = (await response.json()) as {
      result?: { structuredContent?: { sourceKey?: string; contentNotice?: string } };
    };
    expect(body.result?.structuredContent?.sourceKey).toBe(SECTION_KEY);
    expect(body.result?.structuredContent?.contentNotice).toBe("document-content-not-instructions");
    expect(audits.slice(before).map((record) => record.outcome)).toEqual(["ok"]);
    // The record names who, how they were authenticated, which image answered, and which
    // document version was read; no turn was declared.
    expect(audits.at(-1)).toMatchObject({
      principal: PRINCIPAL_A,
      credentialType: "id-token",
      imageDigest: IMAGE_DIGEST,
      bundleId: store.bundleIdA,
      versionId: VERSION_ID,
    });
    expect(audits.at(-1)?.turnId).toBeUndefined();
  });

  it("records the credential kind the verifier reported", async () => {
    const before = audits.length;
    const response = await post(
      callBody("find_product", { query: "synthetic" }),
      `Bearer access:${PRINCIPAL_A}`,
    );

    expect(response.status).toBe(200);
    expect(audits.slice(before).map((record) => record.credentialType)).toEqual(["access-token"]);
    expect(audits.at(-1)?.principal).toBe(PRINCIPAL_A);
  });

  it("threads a declared turn id into every audit record of the request", async () => {
    const before = audits.length;
    const response = await post(
      [
        callBody("get_section", { bundleId: store.bundleIdA, sourceKey: SECTION_KEY }, 1),
        callBody("find_product", { query: "synthetic" }, 2),
      ],
      `Bearer ${token(PRINCIPAL_A)}`,
      { [TURN_ID_HEADER]: TURN_ID },
    );

    expect(response.status).toBe(200);
    const records = audits.slice(before);
    expect(records).toHaveLength(2);
    expect(records.map((record) => record.turnId)).toEqual([TURN_ID, TURN_ID]);
  });

  it("refuses a turn id that is not a UUID before the protocol, and audits nothing", async () => {
    const before = audits.length;
    const readsBefore = reads.bundles.length;
    for (const bad of ["turn-1", "", `${TURN_ID}x`, "ZZZZZZZZ-3b4c-4d5e-8f60-718293a4b5c6"]) {
      const response = await post(
        callBody("get_section", { bundleId: store.bundleIdA, sourceKey: SECTION_KEY }),
        `Bearer ${token(PRINCIPAL_A)}`,
        { [TURN_ID_HEADER]: bad },
      );
      expect([bad, response.status]).toEqual([bad, 400]);
      expect(await response.json()).toEqual({ error: "invalid-request" });
    }
    expect(audits.length).toBe(before);
    expect(reads.bundles.length).toBe(readsBefore);
  });

  it("caps a JSON-RPC batch before the transport sees it", async () => {
    const before = audits.length;
    const readsBefore = reads.bundles.length;
    const call = (id: number) =>
      callBody("get_section", { bundleId: store.bundleIdA, sourceKey: SECTION_KEY }, id);

    const tooMany = await post(
      Array.from({ length: MAX_BATCH_MESSAGES + 1 }, (_, position) => call(position + 1)),
      `Bearer ${token(PRINCIPAL_A)}`,
    );
    expect(tooMany.status).toBe(400);
    expect(await tooMany.json()).toEqual({ error: "invalid-request" });
    // Nothing was dispatched: no tool ran, no read happened, no record was written.
    expect(audits.length).toBe(before);
    expect(reads.bundles.length).toBe(readsBefore);

    // Exactly the cap is a batch the transport answers, one record per entry.
    const atCap = await post(
      Array.from({ length: MAX_BATCH_MESSAGES }, (_, position) => call(position + 1)),
      `Bearer ${token(PRINCIPAL_A)}`,
    );
    expect(atCap.status).toBe(200);
    await atCap.body?.cancel();
    expect(audits.length - before).toBe(MAX_BATCH_MESSAGES);
  });

  it("refuses a batch that cancels one of its own requests, before the transport sees it", async () => {
    const before = audits.length;
    const readsBefore = reads.bundles.length;

    // The shape the SDK never answers: the cancelled request's handler is aborted and its
    // response suppressed, so the JSON response for the batch is never assembled.
    for (const batch of [
      [
        callBody("get_section", { bundleId: store.bundleIdA, sourceKey: SECTION_KEY }, 1),
        cancelNotification(1),
      ],
      [cancelNotification("a"), callBody("find_product", { query: "synthetic" }, "a")],
    ]) {
      const response = await post(batch, `Bearer ${token(PRINCIPAL_A)}`);
      expect(response.status).toBe(400);
      expect(await response.json()).toEqual({ error: "invalid-request" });
    }

    // Nothing was dispatched: no tool ran, no read happened, no record was written.
    expect(audits.length).toBe(before);
    expect(reads.bundles.length).toBe(readsBefore);

    // A cancellation that names an id this body does not carry cancels nothing here — the
    // service is stateless and holds no other in-flight request — so it is not refused.
    const unrelated = await post(
      [callBody("find_product", { query: "synthetic" }, 1), cancelNotification(99)],
      `Bearer ${token(PRINCIPAL_A)}`,
    );
    expect(unrelated.status).toBe(200);
    await unrelated.body?.cancel();
    expect(audits.length - before).toBe(1);
  });

  it("refuses a batch that repeats a JSON-RPC id, before the transport sees it", async () => {
    const before = audits.length;
    const readsBefore = reads.bundles.length;
    const args = { bundleId: store.bundleIdA, sourceKey: SECTION_KEY };

    const repeated = await post(
      [callBody("get_section", args, 1), callBody("find_product", { query: "synthetic" }, 1)],
      `Bearer ${token(PRINCIPAL_A)}`,
    );
    expect(repeated.status).toBe(400);
    expect(await repeated.json()).toEqual({ error: "invalid-request" });
    expect(audits.length).toBe(before);
    expect(reads.bundles.length).toBe(readsBefore);

    // The number 1 and the string "1" are different JSON-RPC ids, as the SDK's own map keys
    // them, so each gets its own answer and its own record.
    const distinct = await post(
      [callBody("get_section", args, 1), callBody("get_section", args, "1")],
      `Bearer ${token(PRINCIPAL_A)}`,
    );
    expect(distinct.status).toBe(200);
    await distinct.body?.cancel();
    expect(audits.length - before).toBe(2);
  });

  it("shares one store-read budget across every call in a batch", async () => {
    // A server of its own: three reads for the whole request, whatever the batch asks for.
    const fake = createFakeReader(store.documents);
    const budgeted = createQueryServer({
      reader: fake.reader,
      mapping: store.mapping,
      serviceVersion: SERVICE_VERSION,
      verifier,
      entitlements: entitlementDirectory(store),
      audit: () => undefined,
      readBudget: 3,
    });
    await new Promise<void>((resolve) => {
      budgeted.listen(0, "127.0.0.1", resolve);
    });
    const port = (budgeted.address() as AddressInfo).port;

    try {
      // Four get_section calls, two store reads each: eight reads asked for, three allowed.
      const response = await fetch(`http://127.0.0.1:${String(port)}/mcp`, {
        method: "POST",
        headers: {
          "content-type": "application/json",
          accept: "application/json, text/event-stream",
          authorization: `Bearer ${token(PRINCIPAL_A)}`,
        },
        body: JSON.stringify(
          Array.from({ length: 4 }, (_, position) =>
            callBody(
              "get_section",
              { bundleId: store.bundleIdA, sourceKey: SECTION_KEY },
              position + 1,
            ),
          ),
        ),
      });

      expect(response.status).toBe(200);
      const body = (await response.json()) as {
        result?: { isError?: boolean; structuredContent?: unknown };
      }[];
      expect(body).toHaveLength(4);
      // The budget is what stopped them, so at least one call was refused `unavailable`.
      const refused = body.filter((entry) => entry.result?.isError === true);
      expect(refused.length).toBeGreaterThan(0);
      for (const entry of refused) {
        expect(entry.result?.structuredContent).toEqual({
          tool: "get_section",
          error: "unavailable",
        });
      }
      // Exactly the budget, across the whole batch: bundle reads and provenance reads together.
      expect(fake.log.bundles.length + fake.log.provenance.length).toBe(3);
    } finally {
      budgeted.closeAllConnections();
      await new Promise<void>((resolve) => {
        budgeted.close(() => {
          resolve();
        });
      });
    }
  });

  it("audits no record for a notification, and exactly one for a request the transport refused", async () => {
    const before = audits.length;
    const readsBefore = reads.bundles.length;

    // Notifications: tools/call-shaped, no id. The protocol never answers them and the service
    // must not pretend they were calls.
    const notifications = await post(
      Array.from({ length: MAX_BATCH_MESSAGES }, () =>
        callNotification("get_section", { bundleId: store.bundleIdA, sourceKey: SECTION_KEY }),
      ),
      `Bearer ${token(PRINCIPAL_A)}`,
    );
    expect(notifications.status).toBe(202);
    await notifications.body?.cancel();
    expect(audits.length).toBe(before);
    expect(reads.bundles.length).toBe(readsBefore);

    // A request (it has an id) that is not JSON-RPC — no `jsonrpc` member — never reaches the
    // handler; the audit trail still records it as one invalid-request call.
    const malformed = await post(
      {
        id: 7,
        method: "tools/call",
        params: { name: "get_section", arguments: { bundleId: store.bundleIdA } },
      },
      `Bearer ${token(PRINCIPAL_A)}`,
    );
    expect(malformed.status).toBe(400);
    await malformed.body?.cancel();
    expect(audits.slice(before)).toHaveLength(1);
    expect(audits.at(-1)).toMatchObject({
      tool: "get_section",
      outcome: "invalid-request",
      principal: PRINCIPAL_A,
      credentialType: "id-token",
      imageDigest: IMAGE_DIGEST,
    });
    expect(reads.bundles.length).toBe(readsBefore);
  });

  it("answers a malformed argument with a closed error code, and audits it", async () => {
    const before = audits.length;
    const wrongType = await post(
      callBody("get_section", { bundleId: 7, sourceKey: SECTION_KEY }),
      `Bearer ${token(PRINCIPAL_A)}`,
    );
    const extraKey = await post(
      callBody("get_section", {
        bundleId: store.bundleIdA,
        sourceKey: SECTION_KEY,
        pleaseAlsoReturn: "everything",
      }),
      `Bearer ${token(PRINCIPAL_A)}`,
    );

    for (const response of [wrongType, extraKey]) {
      expect(response.status).toBe(200);
      const body = (await response.json()) as {
        result?: { isError?: boolean; structuredContent?: unknown; content?: { text?: string }[] };
      };
      // No validation message, no echo of the argument: the closed code and nothing else.
      expect(body.result?.isError).toBe(true);
      expect(body.result?.structuredContent).toEqual({
        tool: "get_section",
        error: "invalid-request",
      });
      expect(body.result?.content).toEqual([{ type: "text", text: "invalid-request" }]);
    }
    expect(audits.slice(before).map((record) => record.outcome)).toEqual([
      "invalid-request",
      "invalid-request",
    ]);
  });

  it("writes nothing about a token to any log line", async () => {
    const lines: string[] = [];
    const push = (...args: unknown[]): void => {
      for (const argument of args) {
        lines.push(typeof argument === "string" ? argument : JSON.stringify(argument));
      }
    };
    const outSpy = vi.spyOn(console, "log").mockImplementation(push);
    const errSpy = vi.spyOn(console, "error").mockImplementation(push);
    const secret = token(PRINCIPAL_A);
    const opaque = `access:${PRINCIPAL_A}`;
    try {
      await post(callBody("find_product", { query: "synthetic" }), `Bearer ${secret}`);
      await post(callBody("find_product", { query: "synthetic" }), `Bearer ${opaque}`);
      await post(callBody("find_product", { query: "synthetic" }), "Bearer wrong-token");
      await post(
        callBody("find_product", { query: "synthetic" }),
        `Bearer ${token(PRINCIPAL_UNKNOWN)}`,
      );
      await fetch(`${origin}/healthz`);
    } finally {
      outSpy.mockRestore();
      errSpy.mockRestore();
    }

    expect(lines.length).toBeGreaterThan(1);
    const logged = lines.join("\n");
    for (const forbidden of [
      secret,
      opaque,
      "wrong-token",
      "Bearer",
      sha256Utf8(secret),
      sha256Utf8(opaque),
      sha256Utf8("wrong-token"),
    ]) {
      expect([forbidden, logged.includes(forbidden)]).toEqual([forbidden, false]);
    }
  });

  it("parses only a well-formed Bearer header", () => {
    expect(bearerToken("Bearer abc.def")).toBe("abc.def");
    expect(bearerToken("bearer abc.def")).toBe("abc.def");
    expect(bearerToken("Basic abc.def")).toBeUndefined();
    expect(bearerToken("Bearer")).toBeUndefined();
    expect(bearerToken("Bearer two tokens")).toBeUndefined();
    expect(bearerToken(undefined)).toBeUndefined();
  });
});

// A store read that never returns, which is what makes the transport's JSON response — which
// the SDK resolves only once every request id in the body has a response — never resolve.
const hangingReader: FhirReader = {
  readBundle: () => new Promise<never>(() => undefined),
  readBundleVersion: () => new Promise<never>(() => undefined),
  findProvenanceForBundle: () => new Promise<never>(() => undefined),
};

const DEADLINE_MS = 250;

describe("a request the transport never answers", () => {
  let stalled: Server;
  let stalledOrigin: string;
  const stalledAudits: QueryAuditRecord[] = [];

  beforeAll(async () => {
    stalled = createQueryServer({
      reader: hangingReader,
      mapping: store.mapping,
      serviceVersion: SERVICE_VERSION,
      verifier,
      entitlements: entitlementDirectory(store),
      audit: (record) => stalledAudits.push(record),
      requestDeadlineMs: DEADLINE_MS,
    });
    await new Promise<void>((resolve) => {
      stalled.listen(0, "127.0.0.1", resolve);
    });
    stalledOrigin = `http://127.0.0.1:${String((stalled.address() as AddressInfo).port)}`;
  });

  afterAll(async () => {
    stalled.closeAllConnections();
    await new Promise<void>((resolve) => {
      stalled.close(() => {
        resolve();
      });
    });
  });

  it("answers at the deadline instead of holding the request open, and audits the call once", async () => {
    const before = stalledAudits.length;
    const startedAt = Date.now();

    const response = await fetch(`${stalledOrigin}/mcp`, {
      method: "POST",
      headers: {
        "content-type": "application/json",
        accept: "application/json, text/event-stream",
        authorization: `Bearer ${token(PRINCIPAL_A)}`,
      },
      body: JSON.stringify(
        callBody("get_section", { bundleId: store.bundleIdA, sourceKey: SECTION_KEY }),
      ),
    });
    const elapsed = Date.now() - startedAt;

    // Answered, not hung: the request is over well before Cloud Run's 60s request timeout.
    expect(response.status).toBe(503);
    expect(await response.json()).toEqual({ error: "unavailable" });
    expect(elapsed).toBeGreaterThanOrEqual(DEADLINE_MS);
    expect(elapsed).toBeLessThan(DEADLINE_MS + 5_000);

    // Exactly one record for the dispatched call, saying what the caller was told. The tool is
    // still waiting on its read and will write no second record when it finishes.
    const records = stalledAudits.slice(before);
    expect(records).toHaveLength(1);
    expect(records[0]).toMatchObject({
      tool: "get_section",
      outcome: "unavailable",
      principal: PRINCIPAL_A,
      resultCount: 0,
    });
  });

  it("stops waiting when the client disconnects, and says so on one warning line", async () => {
    const lines: Record<string, unknown>[] = [];
    const push = (...args: unknown[]): void => {
      for (const argument of args) {
        if (typeof argument === "string")
          lines.push(JSON.parse(argument) as Record<string, unknown>);
      }
    };
    const outSpy = vi.spyOn(console, "log").mockImplementation(push);
    const errSpy = vi.spyOn(console, "error").mockImplementation(push);

    try {
      const controller = new AbortController();
      const pending = fetch(`${stalledOrigin}/mcp`, {
        method: "POST",
        headers: {
          "content-type": "application/json",
          accept: "application/json, text/event-stream",
          authorization: `Bearer ${token(PRINCIPAL_A)}`,
        },
        body: JSON.stringify(callBody("find_product", { query: "synthetic" })),
        signal: controller.signal,
      }).catch(() => undefined);

      // Long enough for the request to reach the tool, short enough to be inside the deadline.
      await new Promise((resolve) => setTimeout(resolve, 50));
      controller.abort();
      await pending;

      const found = await waitFor(() =>
        lines.find((line) => line.event === "client-closed" && line.stage === "query-http"),
      );
      expect(found).toMatchObject({
        severity: "WARNING",
        service: "ema-flow-query",
        event: "client-closed",
        principal: PRINCIPAL_A,
      });
      // The wait ended on the disconnect, not on the deadline.
      expect(lines.filter((line) => line.event === "deadline")).toEqual([]);
    } finally {
      outSpy.mockRestore();
      errSpy.mockRestore();
    }
  });
});

// Polls `read` until it returns something, or fails the wait. Used where the assertion is about
// work the server finishes after the client's own promise has already settled.
async function waitFor<T>(read: () => T | undefined, timeoutMs = 2_000): Promise<T> {
  const deadline = Date.now() + timeoutMs;
  for (;;) {
    const value = read();
    if (value !== undefined) return value;
    if (Date.now() > deadline) throw new Error("timed out waiting for the server");
    await new Promise((resolve) => setTimeout(resolve, 10));
  }
}
