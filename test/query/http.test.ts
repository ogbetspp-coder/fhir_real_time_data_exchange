import type { AddressInfo } from "node:net";
import type { Server } from "node:http";

import { afterAll, beforeAll, describe, expect, it, vi } from "vitest";

import { loadEmaMapping } from "../../src/fhir/mapping.js";
import type { QueryAuditRecord } from "../../src/contracts/query-tools.js";
import { createQueryServer } from "../../src/query/app.js";
import { bearerToken, type IdTokenVerifier } from "../../src/query/auth.js";
import {
  PRINCIPAL_A,
  SECTION_KEY,
  SERVICE_VERSION,
  buildQueryStore,
  createFakeReader,
  entitlementDirectory,
  type QueryStore,
} from "./fixtures.js";

// One real HTTP round trip on an ephemeral port, with an injected verifier: the Bearer check,
// the health endpoint, and the fact that nothing about a token ever reaches a log line.

const AUDIENCE = "https://query.ema-flow.invalid";

// Stands in for Google's ID token verification: same contract, same audience rule, no network.
const verifier: IdTokenVerifier = {
  verify(idToken: string): Promise<string | undefined> {
    const match = /^aud=([^;]+);sub=([A-Za-z0-9]+)$/.exec(idToken);
    if (match?.[1] !== AUDIENCE) return Promise.resolve(undefined);
    return Promise.resolve(match[2]);
  },
};

function token(subject: string, audience = AUDIENCE): string {
  return `aud=${audience};sub=${subject}`;
}

let store: QueryStore;
let server: Server;
let origin: string;
const audits: QueryAuditRecord[] = [];

beforeAll(async () => {
  store = buildQueryStore(await loadEmaMapping());
  const { reader } = createFakeReader(store.documents);
  server = createQueryServer({
    reader,
    mapping: store.mapping,
    serviceVersion: SERVICE_VERSION,
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

async function post(body: unknown, authorization?: string): Promise<Response> {
  return fetch(`${origin}/mcp`, {
    method: "POST",
    headers: {
      "content-type": "application/json",
      accept: "application/json, text/event-stream",
      ...(authorization === undefined ? {} : { authorization }),
    },
    body: JSON.stringify(body),
  });
}

function callBody(name: string, args: Record<string, unknown>): unknown {
  return { jsonrpc: "2.0", id: 1, method: "tools/call", params: { name, arguments: args } };
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
    expect(audits.at(-1)?.principal).toBe(PRINCIPAL_A);
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
    try {
      await post(callBody("find_product", { query: "synthetic" }), `Bearer ${secret}`);
      await post(callBody("find_product", { query: "synthetic" }), "Bearer wrong-token");
      await fetch(`${origin}/healthz`);
    } finally {
      outSpy.mockRestore();
      errSpy.mockRestore();
    }

    const logged = lines.join("\n");
    expect(logged.includes(secret)).toBe(false);
    expect(logged.includes("wrong-token")).toBe(false);
    expect(logged.includes("Bearer")).toBe(false);
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
