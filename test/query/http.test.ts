import type { AddressInfo } from "node:net";
import type { Server } from "node:http";

import { afterAll, beforeAll, describe, expect, it, vi } from "vitest";

import { loadEmaMapping } from "../../src/fhir/mapping.js";
import type { QueryAuditRecord } from "../../src/contracts/query-tools.js";
import { sha256Utf8 } from "../../src/lib/hash.js";
import { MAX_BATCH_MESSAGES, TURN_ID_HEADER, createQueryServer } from "../../src/query/app.js";
import { bearerToken, type CredentialVerifier } from "../../src/query/auth.js";
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
