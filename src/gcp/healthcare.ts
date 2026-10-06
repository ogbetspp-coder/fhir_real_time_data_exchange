import { GoogleAuth } from "google-auth-library";

import type { AppConfig } from "../config.js";
import { documentBundleFullUrl } from "../fhir/transform.js";
import type { FhirBundle, FhirResource, OperationOutcome } from "../fhir/types.js";
import { sha256Utf8 } from "../lib/hash.js";

// Which call was refused, as a closed set. Carried on the error so an operator learns that the
// Healthcare API refused a write rather than, say, KMS refusing a signature — a distinction the
// first failed run could not make, because every upstream refusal arrived as a bare `Error` and
// the pipeline could only call it `unclassified`.
export type HealthcareOperation = "read-source" | "read-target" | "validate" | "execute-bundle";

// An OperationOutcome's `diagnostics` quotes the content that was rejected, so it is never
// carried. `issue[].code` is a closed FHIR value set, and `details.text` is a machine code such
// as `invalid_full_url` — but only when it looks like one. Anything that does not match this
// shape is dropped rather than risk carrying prose into a log.
const MACHINE_CODE = /^[a-z][a-z0-9_]{0,62}$/;

// How long one Healthcare API call may take before the run gives up on it. Without a bound a
// hung call held the request until Cloud Run's 1800 s timeout.
export const HEALTHCARE_TIMEOUT_MS = 120_000;

export class HealthcareApiError extends Error {
  public override readonly name = "HealthcareApiError";

  public constructor(
    public readonly operation: HealthcareOperation,
    public readonly status: number,
    public readonly codes: string[],
    // SHA-256 of the response body's bytes, whatever they were: an OperationOutcome, or a
    // front end's HTML error page.
    public readonly responseSha256: string,
  ) {
    // Status and issue count only: the message reaches logs, and the body may quote content.
    super(
      `Healthcare API refused ${operation} with ${status} (response sha256 ${responseSha256}, ${codes.length} codes)`,
    );
  }
}

// The transaction a run persists, built as a pure function so it can be asserted without a
// credential or a network (ADR 0004: pure libraries are shared by import). The pipeline hashes
// exactly this object, signs the hash into the run manifest, and sends exactly this object. Its
// entries are in dependency order (below).
//
// Entries carry no fullUrl. It is optional on a transaction entry, and `request.url` already
// names the resource unambiguously. (It used to be `urn:uuid:${resource.id}` when most ids were
// readable identifiers rather than UUIDs, and the Cloud Healthcare API refused the whole
// transaction with `invalid_full_url`.)
//
// References are resolved here, by the client, rather than by the server from `urn:uuid`
// fullUrls. The transform names every resource by a `urn:uuid` fullUrl and every reference
// between them by one (src/fhir/transform.ts, D7); inside the document Bundle that is correct, a
// document resolves its references against its own entries. The same resources are also written
// on their own, and there a `urn:uuid` resolves to nothing: Composition.subject, the
// authorisation's holder, an ingredient's `for` and the List's entry each held a literal
// `urn:uuid:` string that no join, `_include` or referential-integrity check could follow. So
// each standalone resource has every reference rewritten to the `Type/id` of the transaction
// entry it names, and the document Bundle is written unchanged. Doing it here, not by giving the
// entries `urn:uuid` fullUrls for the server to resolve, keeps what is stored identical to what
// was hashed and signed, and cannot reach inside the document Bundle, whose own entries carry
// the same `urn:uuid` fullUrls. A reference that names nothing in the transaction is refused.
// A transaction entry is a different shape from a document or collection entry: it carries the
// `request` that says what to do with the resource, and no `fullUrl`. Typing it separately keeps
// `BundleEntry.fullUrl` required where it genuinely is — every entry of a Type 2 source bundle
// and of an EMA document bundle has one, and `src/fhir/preflight.ts` and `src/fhir/provenance.ts`
// both rely on that.
export type PersistTransactionEntry = {
  resource: FhirResource;
  request: { method: "PUT"; url: string; ifMatch?: string; ifNoneMatch?: "*" };
};

// What the target store held of the document Bundle when the run read it, before it signed: the
// version it read, or nothing. It is the transaction's precondition (docs/design/version-identity.md):
// the document Bundle's entry carries `ifMatch` with that version, so the transaction is refused if
// anything wrote the document since, or `ifNoneMatch: "*"`, so it is refused if anything created
// it since. Every resource the transaction writes has an id derived from the same source identifier
// as the Bundle's (src/fhir/transform.ts, D7), every run that writes them writes the Bundle in the
// same transaction, and a transaction is atomic, so the one precondition guards them all.
export type StoredDocument = { versionId: string } | "absent";

export type PersistTransaction = {
  resourceType: "Bundle";
  identifier: { system: string; value: string };
  type: "transaction";
  timestamp: string;
  entry: PersistTransactionEntry[];
};

function address(resource: FhirResource): string {
  if (resource.id === undefined) {
    throw new Error("Every persisted resource requires an id for idempotent persistence");
  }
  return `${resource.resourceType}/${resource.id}`;
}

function resolveReferences(
  resource: FhirResource,
  addresses: ReadonlyMap<string, string>,
  entries: ReadonlySet<string>,
): FhirResource {
  const walk = (node: unknown): unknown => {
    if (Array.isArray(node)) return node.map(walk);
    if (node === null || typeof node !== "object") return node;
    return Object.fromEntries(
      Object.entries(node as Record<string, unknown>).map(([key, child]) => {
        if (key !== "reference" || typeof child !== "string") return [key, walk(child)];
        const resolved = addresses.get(child) ?? (entries.has(child) ? child : undefined);
        if (resolved === undefined) {
          throw new Error("A persisted reference names no resource in the transaction");
        }
        return [key, resolved];
      }),
    );
  };
  return walk(resource) as FhirResource;
}

// Every `Type/id` a resource references, other than itself.
function referencedEntries(resource: FhirResource, entries: ReadonlySet<string>): Set<string> {
  const found = new Set<string>();
  const walk = (node: unknown): void => {
    if (Array.isArray(node)) {
      node.forEach(walk);
      return;
    }
    if (node === null || typeof node !== "object") return;
    for (const [key, child] of Object.entries(node as Record<string, unknown>)) {
      if (key === "reference" && typeof child === "string" && entries.has(child)) found.add(child);
      else walk(child);
    }
  };
  walk(resource);
  found.delete(address(resource));
  return found;
}

// The entries in dependency order: every resource after each resource it references, so no
// entry's reference points forward to one the server has not yet written. (The document Bundle
// references nothing by `Type/id`; its nested `urn:uuid` references resolve inside it.) Among the
// resources whose references are all placed, the earliest in the given order goes first, so the
// order is a pure function of the input. A cycle cannot be ordered and is refused.
function dependencyOrder(resources: FhirResource[]): FhirResource[] {
  const entries = new Set(resources.map(address));
  const needs = resources.map((resource) => referencedEntries(resource, entries));
  const placed = new Set<string>();
  const ordered: FhirResource[] = [];
  while (ordered.length < resources.length) {
    const next = resources.findIndex(
      (resource, index) =>
        !placed.has(address(resource)) &&
        [...(needs[index] ?? [])].every((needed) => placed.has(needed)),
    );
    const resource = resources[next];
    if (resource === undefined) {
      throw new Error("The persisted resources reference each other in a cycle");
    }
    placed.add(address(resource));
    ordered.push(resource);
  }
  return ordered;
}

export function buildPersistTransaction(
  list: FhirResource,
  documentBundle: FhirBundle,
  runId: string,
  stored: StoredDocument,
  extras: FhirResource[] = [],
  timestamp: string = new Date().toISOString(),
): PersistTransaction {
  const documentEntries = documentBundle.entry.map(({ resource }) => resource);
  const all = [list, ...documentEntries, documentBundle, ...extras];
  const entries = new Set(all.map(address));
  // Every fullUrl a reference may use, to the transaction entry it names.
  const addresses = new Map<string, string>(
    documentBundle.entry.map(({ fullUrl, resource }) => [fullUrl, address(resource)]),
  );
  addresses.set(documentBundleFullUrl(documentBundle.id ?? ""), address(documentBundle));

  const standalone = (resource: FhirResource): FhirResource =>
    resolveReferences(resource, addresses, entries);
  const resources = dependencyOrder([
    standalone(list),
    ...documentEntries.map(standalone),
    documentBundle,
    ...extras.map(standalone),
  ]);

  return {
    resourceType: "Bundle",
    identifier: {
      system: "https://khs.dev/fhir/identifier/transaction",
      value: runId,
    },
    type: "transaction",
    timestamp,
    entry: resources.map((resource) => ({
      resource,
      request: {
        method: "PUT",
        url: address(resource),
        ...(resource !== documentBundle
          ? {}
          : stored === "absent"
            ? { ifNoneMatch: "*" as const }
            : { ifMatch: `W/"${stored.versionId}"` }),
      },
    })),
  };
}

// `lastUpdated` is absent when the response gives neither `lastModified` nor the resource's meta:
// the version id alone is what the stream check needs.
export type PersistedVersion = { versionId: string; lastUpdated?: string };

type ResponseEntry = {
  response?: { location?: unknown; etag?: unknown; lastModified?: unknown };
  resource?: { resourceType?: unknown; id?: unknown; meta?: { lastUpdated?: unknown } };
};

// The version a transaction wrote of one resource, read from the transaction-response: the
// entry whose `location` names `Type/id/_history/<versionId>`, and its `lastModified` if given. What the
// workflow's BigQuery check filters on, so it proves this run's version streamed rather than any
// earlier version of the same id. Undefined when the response does not say.
export function persistedVersion(
  response: unknown,
  resourceType: string,
  id: string,
): PersistedVersion | undefined {
  const entries: unknown = (response as { entry?: unknown } | null | undefined)?.entry;
  if (!Array.isArray(entries)) return undefined;
  const history = `${resourceType}/${id}/_history/`;
  for (const entry of entries as ResponseEntry[]) {
    const location = entry.response?.location;
    if (typeof location !== "string") continue;
    const at = location.lastIndexOf(history);
    if (at === -1 || (at > 0 && location[at - 1] !== "/")) continue;
    const versionId = location.slice(at + history.length);
    if (versionId.length === 0 || versionId.includes("/")) return undefined;
    const lastUpdated = entry.response?.lastModified ?? entry.resource?.meta?.lastUpdated;
    return typeof lastUpdated === "string" ? { versionId, lastUpdated } : { versionId };
  }
  return undefined;
}

// Whether a body is an OperationOutcome's issues worth reading codes from.
function issueCodes(body: unknown): string[] {
  const issues = (body as { issue?: unknown } | null | undefined)?.issue;
  return (Array.isArray(issues) ? issues : [])
    .flatMap((issue: unknown) => {
      const { code, details } = (issue ?? {}) as { code?: unknown; details?: unknown };
      const text = (details as { text?: unknown } | undefined)?.text;
      return [code, text];
    })
    .filter((value): value is string => typeof value === "string" && MACHINE_CODE.test(value));
}

type HealthcareClientOptions = Pick<
  AppConfig,
  | "GOOGLE_CLOUD_PROJECT"
  | "GCP_LOCATION"
  | "HEALTHCARE_DATASET_ID"
  | "SOURCE_FHIR_STORE_ID"
  | "TARGET_FHIR_STORE_ID"
>;

export class HealthcareApiClient {
  readonly #auth = new GoogleAuth({
    scopes: ["https://www.googleapis.com/auth/cloud-platform"],
  });

  public constructor(private readonly options: HealthcareClientOptions) {}

  #storeBase(storeId: string): string {
    const project = this.options.GOOGLE_CLOUD_PROJECT;
    const dataset = this.options.HEALTHCARE_DATASET_ID;
    if (project === undefined || dataset === undefined) {
      throw new Error("Healthcare API project and dataset configuration are required");
    }
    return [
      "https://healthcare.googleapis.com/v1/projects",
      encodeURIComponent(project),
      "locations",
      encodeURIComponent(this.options.GCP_LOCATION),
      "datasets",
      encodeURIComponent(dataset),
      "fhirStores",
      encodeURIComponent(storeId),
      "fhir",
    ].join("/");
  }

  async #request<T>(
    operation: HealthcareOperation,
    url: string,
    runId: string,
    init: RequestInit = {},
  ): Promise<T> {
    const token = await this.#auth.getAccessToken();
    if (token === null) throw new Error("Application Default Credentials returned no access token");

    const headers = new Headers(init.headers);
    headers.set("authorization", `Bearer ${token}`);
    headers.set("content-type", "application/fhir+json; charset=utf-8");
    headers.set("x-request-id", runId);
    headers.set("x-goog-healthcare-audit-appname", "ema-flow");
    headers.set("x-goog-healthcare-audit-reason", "ePI interoperability pipeline");

    // The body is read as text before anything is concluded from it: a front end's HTML 502, 503
    // or 429 used to become a SyntaxError from `response.json()`, losing the operation and the
    // status, with upstream text in its message.
    let status: number;
    let ok: boolean;
    let text: string;
    try {
      const response = await fetch(url, {
        ...init,
        headers,
        signal: AbortSignal.timeout(HEALTHCARE_TIMEOUT_MS),
      });
      ({ status, ok } = response);
      text = await response.text();
    } catch (error) {
      if (error instanceof DOMException && error.name === "TimeoutError") {
        throw new Error("Healthcare API request timed out", { cause: error });
      }
      throw error;
    }

    let body: unknown;
    try {
      body = JSON.parse(text);
    } catch {
      body = undefined;
    }
    if (!ok) {
      // The body may quote FHIR content, so only the closed parts of it travel: each issue's
      // `code` (a FHIR value set) and its `details.text` when that is a machine code.
      throw new HealthcareApiError(
        operation,
        status,
        [...new Set(issueCodes(body))],
        sha256Utf8(text),
      );
    }
    if (body === null || typeof body !== "object") {
      throw new Error("Healthcare API returned a response that is not a JSON object");
    }
    return body as T;
  }

  public async readSourceResource<T extends FhirResource>(
    resourceType: string,
    id: string,
    runId: string,
  ): Promise<T> {
    const store = this.options.SOURCE_FHIR_STORE_ID;
    if (store === undefined) throw new Error("SOURCE_FHIR_STORE_ID is required");
    const address = `/${encodeURIComponent(resourceType)}/${encodeURIComponent(id)}`;
    const url = `${this.#storeBase(store)}${address}`;
    // Encoding leaves `.` and `..` as they are, and the URL parser resolves them: `Bundle/..` would
    // read the store itself. The id must name the resource, so the resolved path must end with it.
    if (!new URL(url).pathname.endsWith(address)) {
      throw new Error("A source resource id must be a single path segment");
    }
    return this.#request<T>("read-source", url, runId);
  }

  // The version of a resource the target store holds now, read before a run signs its
  // transaction, or "absent" when the store answers 404. A deleted resource (410) or any other
  // refusal fails the run: what to write over it is for a person to decide.
  public async readStoredVersion(
    resourceType: string,
    id: string,
    runId: string,
  ): Promise<StoredDocument> {
    const store = this.options.TARGET_FHIR_STORE_ID;
    if (store === undefined) throw new Error("TARGET_FHIR_STORE_ID is required");
    const url = `${this.#storeBase(store)}/${encodeURIComponent(resourceType)}/${encodeURIComponent(id)}`;
    let resource: FhirResource;
    try {
      resource = await this.#request<FhirResource>("read-target", url, runId, { method: "GET" });
    } catch (error) {
      if (error instanceof HealthcareApiError && error.status === 404) return "absent";
      throw error;
    }
    const versionId = resource.meta?.versionId;
    if (typeof versionId !== "string" || versionId.length === 0) {
      throw new Error("The target store answered a resource without a version id");
    }
    return { versionId };
  }

  public async validate(
    resource: FhirResource,
    profile: string,
    runId: string,
  ): Promise<OperationOutcome> {
    const store = this.options.TARGET_FHIR_STORE_ID;
    if (store === undefined) throw new Error("TARGET_FHIR_STORE_ID is required");
    const query = new URLSearchParams({ profile });
    const url = `${this.#storeBase(store)}/${resource.resourceType}/$validate?${query.toString()}`;
    return this.#request<OperationOutcome>("validate", url, runId, {
      method: "POST",
      body: JSON.stringify(resource),
    });
  }

  // Sends exactly the transaction given: the pipeline builds it with buildPersistTransaction and
  // has already signed its hash.
  public async executeTransaction(
    transaction: PersistTransaction,
    runId: string,
  ): Promise<FhirBundle> {
    const store = this.options.TARGET_FHIR_STORE_ID;
    if (store === undefined) throw new Error("TARGET_FHIR_STORE_ID is required");

    return this.#request<FhirBundle>("execute-bundle", this.#storeBase(store), runId, {
      method: "POST",
      body: JSON.stringify(transaction),
    });
  }
}
