import type { EmaMapping } from "../fhir/mapping.js";
import { syntheticPublication, SYNTHETIC_DOCUMENT_ID, SYNTHETIC_INDEX_ID } from "./synthetic.js";

// Zone B fetches the authority's files itself (docs/design/authority-import-contract.md, D1):
// from a fixed URL template on an allowlisted host, with exactly one Accept header, no redirect
// followed, HTTP 200 only, a timeout and a size limit on the decoded body. The synthetic
// authority is "fetched" from the code that builds it, and only where the deployment accepts
// synthetic content.

export class AuthorityFetchError extends Error {
  public constructor(public readonly reason: string) {
    super(`Authority fetch refused: ${reason}`);
    this.name = "AuthorityFetchError";
  }
}

export type AuthorityFile = { kind: "document" | "index"; id: string };

export type Fetched = { url: string; bytes: Uint8Array; fetchedAt: string };

export type AuthorityFetcher = {
  fetch: (authority: "EMA" | "synthetic", file: AuthorityFile) => Promise<Fetched>;
};

const GUID = /^[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}$/;
const EMA_HOST = "epi.ema.europa.eu";
const EMA_BASE = `https://${EMA_HOST}/consuming/api/fhir`;
export const MAX_AUTHORITY_BYTES = 4 * 1024 * 1024;
const TIMEOUT_MS = 30_000;

export function authorityUrl(authority: "EMA" | "synthetic", file: AuthorityFile): string {
  if (!GUID.test(file.id)) throw new AuthorityFetchError("id-not-a-guid");
  const resource = file.kind === "document" ? "Bundle" : "List";
  return authority === "EMA"
    ? `${EMA_BASE}/${resource}/${file.id}`
    : `https://synthetic.invalid/${resource}/${file.id}`;
}

// The timeout's abort, before the headers or while the body is read.
function timedOut(error: unknown): boolean {
  return (error as { name?: unknown } | null)?.name === "TimeoutError";
}

type FetchFunction = (url: string, init: RequestInit) => Promise<Response>;

// The EMA's files over HTTPS. `fetchFunction` and `now` are injectable for tests only.
export function emaFetcher(
  fetchFunction: FetchFunction = fetch,
  now: () => Date = () => new Date(),
): AuthorityFetcher["fetch"] {
  return async (authority, file) => {
    if (authority !== "EMA") throw new AuthorityFetchError("not-the-ema");
    const url = authorityUrl(authority, file);
    const resolved = new URL(url);
    if (resolved.protocol !== "https:" || resolved.host !== EMA_HOST) {
      throw new AuthorityFetchError("host-not-allowed");
    }
    let response: Response;
    try {
      response = await fetchFunction(url, {
        // The EMA serves XML, or an error, for any other value.
        headers: { Accept: "application/fhir+json" },
        redirect: "error",
        signal: AbortSignal.timeout(TIMEOUT_MS),
      });
    } catch (error) {
      throw new AuthorityFetchError(timedOut(error) ? "timeout" : "unreachable");
    }
    if (response.status !== 200) {
      // The body is not read, so it is released rather than left holding the connection; not
      // awaited, so a cancel that never settles cannot hold the fetch.
      void response.body?.cancel().catch(() => undefined);
      throw new AuthorityFetchError(`http-${String(response.status)}`);
    }
    const body = response.body;
    if (body === null) throw new AuthorityFetchError("empty-body");
    const chunks: Uint8Array[] = [];
    let length = 0;
    const reader = (body as ReadableStream<Uint8Array>).getReader();
    for (;;) {
      let read: Awaited<ReturnType<typeof reader.read>>;
      try {
        read = await reader.read();
      } catch (error) {
        // A body cut off or timed out mid-read is a refusal like any other, never an exception.
        throw new AuthorityFetchError(timedOut(error) ? "timeout" : "unreachable");
      }
      if (read.done) break;
      length += read.value.length;
      if (length > MAX_AUTHORITY_BYTES) {
        await reader.cancel();
        throw new AuthorityFetchError("too-large");
      }
      chunks.push(read.value);
    }
    const bytes = new Uint8Array(length);
    let offset = 0;
    for (const chunk of chunks) {
      bytes.set(chunk, offset);
      offset += chunk.length;
    }
    return { url, bytes, fetchedAt: now().toISOString() };
  };
}

// The synthetic authority's files, built in code. Refused unless synthetic content is allowed.
export function syntheticFetcher(
  mapping: EmaMapping,
  allowSyntheticSources: boolean,
  now: () => Date = () => new Date(),
): AuthorityFetcher["fetch"] {
  return (authority, file) => {
    if (authority !== "synthetic") return Promise.reject(new AuthorityFetchError("not-synthetic"));
    if (!allowSyntheticSources) {
      return Promise.reject(new AuthorityFetchError("synthetic-sources-not-allowed"));
    }
    const publication = syntheticPublication(mapping);
    const known = file.kind === "document" ? SYNTHETIC_DOCUMENT_ID : SYNTHETIC_INDEX_ID;
    if (file.id !== known) return Promise.reject(new AuthorityFetchError("http-404"));
    return Promise.resolve({
      url: authorityUrl(authority, file),
      bytes: file.kind === "document" ? publication.document : publication.index,
      fetchedAt: now().toISOString(),
    });
  };
}

// The gate's fetcher: the EMA over the network, the synthetic authority from code.
export function defaultFetcher(
  mapping: EmaMapping,
  allowSyntheticSources: boolean,
): AuthorityFetcher {
  const ema = emaFetcher();
  const synthetic = syntheticFetcher(mapping, allowSyntheticSources);
  return {
    fetch: (authority, file) =>
      authority === "EMA" ? ema(authority, file) : synthetic(authority, file),
  };
}
