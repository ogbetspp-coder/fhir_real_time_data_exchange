import { randomUUID } from "node:crypto";

import { GoogleAuth } from "google-auth-library";

import type { FhirBundle, FhirResource } from "../fhir/types.js";
import { sha256, sha256Utf8 } from "../lib/hash.js";
import type { QueryConfig } from "./config.js";

// Reads of the validated store, and nothing else. The request pattern follows
// src/gcp/healthcare.ts — including its hash-only error style, because a FHIR error body may
// quote content — but the client is deliberately not shared: the query service must not hold the
// worker's store configuration or its credential scope, and it needs three reads, no writes
// (ADR 0004, points 1 and 3).

// `signal`, when given, is the HTTP request's: aborted once the request is over, so a read for
// an answer nobody will receive is cancelled rather than finished.
export type FhirReader = {
  readBundle(bundleId: string, signal?: AbortSignal): Promise<FhirBundle | undefined>;
  readBundleVersion(
    bundleId: string,
    versionId: string,
    signal?: AbortSignal,
  ): Promise<FhirBundle | undefined>;
  findProvenanceForBundle(
    bundleId: string,
    signal?: AbortSignal,
  ): Promise<FhirResource | undefined>;
};

// `httpStatus` is the store's answer when there was one, so the operator's log can say which
// refusal it was without the message, which quotes nothing but is not logged either.
export class FhirReadError extends Error {
  public constructor(
    message: string,
    public readonly httpStatus?: number,
  ) {
    super(message);
    this.name = "FhirReadError";
  }
}

// How long one store read may take, whatever the request's own deadline: a read the store never
// answers is abandoned here rather than holding a place in the request's read pool until the
// request is over.
export const STORE_READ_TIMEOUT_MS = 10_000;

// Settles with `work`, or rejects with the signal's reason once it is aborted, whichever comes
// first. For a call that takes no signal of its own (google-auth-library's getAccessToken): the
// read stops waiting, and the library finishes or abandons its own request, which it caches
// for the next read either way.
function untilAborted<T>(work: Promise<T>, signal: AbortSignal): Promise<T> {
  if (signal.aborted) return Promise.reject(signal.reason as Error);
  return new Promise<T>((resolve, reject) => {
    const onAbort = (): void => {
      reject(signal.reason as Error);
    };
    signal.addEventListener("abort", onAbort, { once: true });
    work.then(
      (value) => {
        signal.removeEventListener("abort", onAbort);
        resolve(value);
      },
      (error: unknown) => {
        signal.removeEventListener("abort", onAbort);
        reject(error instanceof Error ? error : new Error("credential lookup failed"));
      },
    );
  });
}

type ReaderOptions = Pick<
  QueryConfig,
  "GOOGLE_CLOUD_PROJECT" | "GCP_LOCATION" | "HEALTHCARE_DATASET_ID" | "TARGET_FHIR_STORE_ID"
>;

function isBundle(resource: FhirResource): resource is FhirBundle {
  return resource.resourceType === "Bundle" && Array.isArray(resource.entry);
}

// Bounded, because a page is not a scan. The page holds every Provenance of a document unless it
// has more approvals than this, and only then does the page's own order decide what is on it. A
// document gains one Provenance per approved version, so twenty is a wide margin, and the bound
// is what stops a pathological document turning one tool call into an unbounded read.
export const PROVENANCE_PAGE_SIZE = 20;

// When the store wrote the resource: `meta.lastUpdated`, which the store sets on every write and
// the writer cannot choose. Sorts as a number so that two values written with different UTC
// offsets compare by the instant they name and not by their spelling. A resource whose
// `meta.lastUpdated` is missing or unparseable is not an error — it is simply ranked after
// every dated one, which keeps the order total rather than throwing away the only answer
// available.
function writtenAt(resource: FhirResource): number {
  const lastUpdated: unknown = resource.meta?.lastUpdated;
  if (typeof lastUpdated !== "string") return Number.NEGATIVE_INFINITY;
  const instant = Date.parse(lastUpdated);
  return Number.isNaN(instant) ? Number.NEGATIVE_INFINITY : instant;
}

// A total order over Provenance resources: most recently written first, ties broken by the
// resource id, which is stable and unique within a store. Written, not `recorded`: `recorded` is
// the approval's own date (`approval.approvedAt` from the submission), so a version approved
// earlier but published later — version 1 republished after version 2 — would otherwise lose
// to an approval that is no longer the current version's. The worker writes a version and its
// Provenance in one transaction, so the most recently written approval is the one written with
// the current version whenever that version came through the gate. Exported because the
// property that matters — the same set always yields the same resource — is a property of this
// function, and is tested directly rather than inferred from a search response.
export function latestProvenance(resources: readonly FhirResource[]): FhirResource | undefined {
  let best: FhirResource | undefined;
  for (const candidate of resources) {
    if (best === undefined) {
      best = candidate;
      continue;
    }
    // Compared, not subtracted: two resources that both lack a usable `meta.lastUpdated` are both
    // NEGATIVE_INFINITY, and subtracting those gives NaN, which is greater than nothing and
    // equal to nothing — the tie-break would never run and the order would not be total.
    const candidateInstant = writtenAt(candidate);
    const bestInstant = writtenAt(best);
    if (candidateInstant > bestInstant) {
      best = candidate;
      continue;
    }
    if (candidateInstant === bestInstant && (candidate.id ?? "") < (best.id ?? "")) {
      best = candidate;
    }
  }
  return best;
}

export class HealthcareFhirReader implements FhirReader {
  readonly #auth = new GoogleAuth({
    scopes: ["https://www.googleapis.com/auth/cloud-platform"],
  });

  public constructor(private readonly options: ReaderOptions) {}

  #storeBase(): string {
    return [
      "https://healthcare.googleapis.com/v1/projects",
      encodeURIComponent(this.options.GOOGLE_CLOUD_PROJECT),
      "locations",
      encodeURIComponent(this.options.GCP_LOCATION),
      "datasets",
      encodeURIComponent(this.options.HEALTHCARE_DATASET_ID),
      "fhirStores",
      encodeURIComponent(this.options.TARGET_FHIR_STORE_ID),
      "fhir",
    ].join("/");
  }

  // A missing resource is `undefined`, not an error: the tools turn it into the closed
  // `document-not-found` code without learning anything else about the store.
  async #read<T>(url: string, signal?: AbortSignal): Promise<T | undefined> {
    // One bound for the whole read, the credential included: minting a token is a network call
    // too, and a metadata server that never answers must not hold the read past its timeout or
    // past the end of the request.
    const timeout = AbortSignal.timeout(STORE_READ_TIMEOUT_MS);
    const bound = signal === undefined ? timeout : AbortSignal.any([signal, timeout]);
    const token = await untilAborted(this.#auth.getAccessToken(), bound);
    if (token === null)
      throw new FhirReadError("Application Default Credentials returned no token");

    const response = await fetch(url, {
      method: "GET",
      headers: {
        authorization: `Bearer ${token}`,
        accept: "application/fhir+json; charset=utf-8",
        "x-request-id": randomUUID(),
        "x-goog-healthcare-audit-appname": "ema-flow-query",
        "x-goog-healthcare-audit-reason": "ePI read-only query service",
      },
      signal: bound,
    });

    if (response.status === 404 || response.status === 410) {
      // Nothing is read from a missing resource's body, so it is released rather than left for
      // the connection to drain.
      await response.body?.cancel();
      return undefined;
    }

    // Read as text and parsed here, so a body that is not JSON — an HTML error page from a
    // proxy in front of the store — still fails naming the status rather than as a bare
    // SyntaxError. The body may quote FHIR content; it is referenced by hash and issue count
    // only: the hash of its canonical JSON when it is JSON, of its bytes when it is not.
    const raw = await response.text();
    let body: unknown;
    let parsed = true;
    try {
      body = JSON.parse(raw);
    } catch {
      parsed = false;
    }
    const status = `Healthcare API ${String(response.status)} ${response.statusText}`;
    if (!parsed) {
      throw new FhirReadError(
        `${status} (non-JSON response sha256 ${sha256Utf8(raw)})`,
        response.status,
      );
    }
    if (!response.ok) {
      const issues =
        body !== null && typeof body === "object" ? (body as { issue?: unknown }).issue : undefined;
      const issueCount = Array.isArray(issues) ? issues.length : 0;
      throw new FhirReadError(
        `${status} (response sha256 ${sha256(body)}, ${String(issueCount)} issues)`,
        response.status,
      );
    }
    return body as T;
  }

  public async readBundle(bundleId: string, signal?: AbortSignal): Promise<FhirBundle | undefined> {
    return this.#read<FhirBundle>(
      `${this.#storeBase()}/Bundle/${encodeURIComponent(bundleId)}`,
      signal,
    );
  }

  public async readBundleVersion(
    bundleId: string,
    versionId: string,
    signal?: AbortSignal,
  ): Promise<FhirBundle | undefined> {
    const url = `${this.#storeBase()}/Bundle/${encodeURIComponent(bundleId)}/_history/${encodeURIComponent(versionId)}`;
    return this.#read<FhirBundle>(url, signal);
  }

  // One document can carry more than one Provenance: publishing a second approved version
  // writes a second, and the demonstration set deliberately does exactly that. The previous
  // search asked for `_count=1` in no stated order, so which of them came back was decided by
  // the store rather than by this code, and two identical calls could disagree after a
  // re-seed. The search asks for `recorded` descending — verified honoured by the Healthcare
  // API on 2026-09-21 by reversing it and watching the two paracetamol records swap — over a
  // bounded page, and the winner is chosen here by write order (latestProvenance), so that a
  // version approved earlier but published later still gets its own approval and ties are
  // decided the same way every time. The page is still ordered by `recorded`: asking the store
  // for `-_lastUpdated` instead would put the newest-written first on the page too, but it has
  // not been checked against the Healthcare API the way `-recorded` was, and a sort the store
  // refused would make every lookup fail. Until it is, the write order is exact for a document
  // with at most PROVENANCE_PAGE_SIZE approvals.
  public async findProvenanceForBundle(
    bundleId: string,
    signal?: AbortSignal,
  ): Promise<FhirResource | undefined> {
    const query = new URLSearchParams({
      target: `Bundle/${bundleId}`,
      _sort: "-recorded",
      _count: String(PROVENANCE_PAGE_SIZE),
    });
    const searchSet = await this.#read<FhirBundle>(
      `${this.#storeBase()}/Provenance?${query.toString()}`,
      signal,
    );
    if (searchSet === undefined || !isBundle(searchSet)) return undefined;
    return latestProvenance(
      searchSet.entry
        .map(({ resource }) => resource)
        .filter((resource) => resource.resourceType === "Provenance"),
    );
  }
}
