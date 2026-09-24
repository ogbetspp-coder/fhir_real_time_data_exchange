import { describe, expect, it } from "vitest";

import {
  AuthorityFetchError,
  MAX_AUTHORITY_BYTES,
  authorityUrl,
  defaultFetcher,
  emaFetcher,
  syntheticFetcher,
} from "../../src/authority/fetch.js";
import { SYNTHETIC_DOCUMENT_ID, SYNTHETIC_INDEX_ID } from "../../src/authority/synthetic.js";
import { loadEmaMapping } from "../../src/fhir/mapping.js";

// Zone B fetches the authority's files itself, by fixed rules (docs/design/authority-import-contract.md, D1).

const DOCUMENT = { kind: "document", id: "1286255d-f544-ef11-a317-000d3aaa05e0" } as const;
const NOW = () => new Date("2026-09-24T12:00:00Z");

async function reason(promise: Promise<unknown>): Promise<string> {
  try {
    await promise;
  } catch (error) {
    if (error instanceof AuthorityFetchError) return error.reason;
    throw error;
  }
  return "fetched";
}

describe("the EMA fetcher", () => {
  it("asks the fixed URL for JSON, follows no redirect, and keeps the bytes and the time", async () => {
    let seen: { url: string; init: RequestInit } | undefined;
    const fetcher = emaFetcher((url, init) => {
      seen = { url, init };
      return Promise.resolve(new Response(new Uint8Array([1, 2, 3]), { status: 200 }));
    }, NOW);
    const result = await fetcher("EMA", DOCUMENT);

    expect(seen?.url).toBe(
      "https://epi.ema.europa.eu/consuming/api/fhir/Bundle/1286255d-f544-ef11-a317-000d3aaa05e0",
    );
    expect(seen?.init.headers).toEqual({ Accept: "application/fhir+json" });
    expect(seen?.init.redirect).toBe("error");
    expect(seen?.init.signal).toBeInstanceOf(AbortSignal);
    expect([...result.bytes]).toEqual([1, 2, 3]);
    expect(result.fetchedAt).toBe("2026-09-24T12:00:00.000Z");
    expect(authorityUrl("EMA", { kind: "index", id: DOCUMENT.id })).toContain("/List/");
  });

  it("refuses anything but a 200 with a body within the limit from the EMA", async () => {
    const answering = (response: Response) => emaFetcher(() => Promise.resolve(response), NOW);
    expect(await reason(answering(new Response("x", { status: 404 }))("EMA", DOCUMENT))).toBe(
      "http-404",
    );
    expect(await reason(answering(new Response(null, { status: 200 }))("EMA", DOCUMENT))).toBe(
      "empty-body",
    );
    const large = new Response(new Uint8Array(MAX_AUTHORITY_BYTES + 1), { status: 200 });
    expect(await reason(answering(large)("EMA", DOCUMENT))).toBe("too-large");
    expect(
      await reason(emaFetcher(() => Promise.reject(new Error("down")), NOW)("EMA", DOCUMENT)),
    ).toBe("unreachable");
    expect(await reason(answering(new Response("x", { status: 201 }))("EMA", DOCUMENT))).toBe(
      "http-201",
    );
    const broken = new Response(
      new ReadableStream({
        pull: (controller) => {
          controller.error(new Error("connection reset"));
        },
      }),
      { status: 200 },
    );
    expect(await reason(answering(broken)("EMA", DOCUMENT))).toBe("unreachable");
    const slow = new Response(
      new ReadableStream({
        pull: (controller) => {
          controller.error(new DOMException("timed out", "TimeoutError"));
        },
      }),
      { status: 200 },
    );
    expect(await reason(answering(slow)("EMA", DOCUMENT))).toBe("timeout");
    expect(await reason(answering(new Response("x"))("synthetic", DOCUMENT))).toBe("not-the-ema");
    expect(
      await reason(answering(new Response("x"))("EMA", { kind: "document", id: "../x" })),
    ).toBe("id-not-a-guid");
  });
});

describe("the synthetic fetcher", () => {
  it("serves the synthetic publication only where synthetic content is allowed", async () => {
    const mapping = await loadEmaMapping();
    const allowed = syntheticFetcher(mapping, true, NOW);
    const document = await allowed("synthetic", { kind: "document", id: SYNTHETIC_DOCUMENT_ID });
    const index = await allowed("synthetic", { kind: "index", id: SYNTHETIC_INDEX_ID });

    expect(document.url).toBe(`https://synthetic.invalid/Bundle/${SYNTHETIC_DOCUMENT_ID}`);
    expect(index.bytes.length).toBeGreaterThan(0);
    expect(await reason(allowed("synthetic", { kind: "index", id: SYNTHETIC_DOCUMENT_ID }))).toBe(
      "http-404",
    );
    expect(await reason(allowed("EMA", { kind: "index", id: SYNTHETIC_INDEX_ID }))).toBe(
      "not-synthetic",
    );
    expect(
      await reason(
        syntheticFetcher(
          mapping,
          false,
          NOW,
        )("synthetic", {
          kind: "document",
          id: SYNTHETIC_DOCUMENT_ID,
        }),
      ),
    ).toBe("synthetic-sources-not-allowed");
    const combined = defaultFetcher(mapping, true);
    expect(
      (await combined.fetch("synthetic", { kind: "document", id: SYNTHETIC_DOCUMENT_ID })).url,
    ).toContain("synthetic.invalid");
  });
});
