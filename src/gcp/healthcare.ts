import { GoogleAuth } from "google-auth-library";

import type { AppConfig } from "../config.js";
import type { FhirBundle, FhirResource, OperationOutcome } from "../fhir/types.js";
import { sha256 } from "../lib/hash.js";

// Which call was refused, as a closed set. Carried on the error so an operator learns that the
// Healthcare API refused a write rather than, say, KMS refusing a signature — a distinction the
// first failed run could not make, because every upstream refusal arrived as a bare `Error` and
// the pipeline could only call it `unclassified`.
export type HealthcareOperation = "read-source" | "validate" | "execute-bundle";

// An OperationOutcome's `diagnostics` quotes the content that was rejected, so it is never
// carried. `issue[].code` is a closed FHIR value set, and `details.text` is a machine code such
// as `invalid_full_url` — but only when it looks like one. Anything that does not match this
// shape is dropped rather than risk carrying prose into a log.
const MACHINE_CODE = /^[a-z][a-z0-9_]{0,62}$/;

export class HealthcareApiError extends Error {
  public override readonly name = "HealthcareApiError";

  public constructor(
    public readonly operation: HealthcareOperation,
    public readonly status: number,
    public readonly codes: string[],
    public readonly responseSha256: string,
  ) {
    // Status and issue count only: the message reaches logs, and the body may quote content.
    super(
      `Healthcare API refused ${operation} with ${status} (response sha256 ${responseSha256}, ${codes.length} codes)`,
    );
  }
}

// The transaction a run persists, built as a pure function so it can be asserted without a
// credential or a network (ADR 0004: pure libraries are shared by import).
//
// Entries carry no fullUrl. It is optional on a transaction entry, and `request.url` already
// names the resource unambiguously, so there is nothing for it to add.
//
// It used to be `urn:uuid:${resource.id}`, which is a well-formed URN only when the id happens
// to be a UUID. Most of these ids are not: the product graph carries readable ids such as
// `synthetic-pharma`, and a real label's ids are business identifiers rather than UUIDs, so this
// was never a fixture-only defect. The Cloud Healthcare API refused the whole transaction with
// `invalid_full_url`, which is why no run had ever persisted anything in this project. The
// refusal was atomic, so nothing was ever half-written — nothing was written at all.
//
// Inter-entry references are unaffected: these resources reference each other by absolute https
// URLs, not by the urn:uuid placeholders whose resolution is the one thing fullUrl exists to
// support inside a transaction.
// A transaction entry is a different shape from a document or collection entry: it carries the
// `request` that says what to do with the resource, and no `fullUrl`. Typing it separately keeps
// `BundleEntry.fullUrl` required where it genuinely is — every entry of a Type 2 source bundle
// and of an EMA document bundle has one, and `src/fhir/preflight.ts` and `src/fhir/provenance.ts`
// both rely on that. It also removes an `as BundleEntry` cast that was silently asserting this
// object satisfied a type it did not.
export type PersistTransactionEntry = {
  resource: FhirResource;
  request: { method: "PUT"; url: string };
};

export type PersistTransaction = {
  resourceType: "Bundle";
  identifier: { system: string; value: string };
  type: "transaction";
  timestamp: string;
  entry: PersistTransactionEntry[];
};

export function buildPersistTransaction(
  list: FhirResource,
  documentBundle: FhirBundle,
  runId: string,
  extras: FhirResource[] = [],
): PersistTransaction {
  const resources = [
    list,
    ...documentBundle.entry.map(({ resource }) => resource),
    documentBundle,
    ...extras,
  ];
  const entry: PersistTransactionEntry[] = resources.map((resource) => {
    if (resource.id === undefined) {
      throw new Error(`${resource.resourceType} requires an id for idempotent persistence`);
    }
    return {
      resource,
      request: {
        method: "PUT",
        url: `${resource.resourceType}/${resource.id}`,
      },
    };
  });

  return {
    resourceType: "Bundle",
    identifier: {
      system: "https://khs.dev/fhir/identifier/transaction",
      value: runId,
    },
    type: "transaction",
    timestamp: new Date().toISOString(),
    entry,
  };
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

    const response = await fetch(url, {
      ...init,
      headers,
    });

    const body = (await response.json()) as T;
    if (!response.ok) {
      // The body may quote FHIR content, so only the closed parts of it travel: each issue's
      // `code` (a FHIR value set) and its `details.text` when that is a machine code.
      const issues = (body as { issue?: unknown }).issue;
      const codes = (Array.isArray(issues) ? issues : [])
        .flatMap((issue: unknown) => {
          const { code, details } = (issue ?? {}) as { code?: unknown; details?: unknown };
          const text = (details as { text?: unknown } | undefined)?.text;
          return [code, text];
        })
        .filter((value): value is string => typeof value === "string" && MACHINE_CODE.test(value));
      throw new HealthcareApiError(operation, response.status, [...new Set(codes)], sha256(body));
    }
    return body;
  }

  public async readSourceResource<T extends FhirResource>(
    resourceType: string,
    id: string,
    runId: string,
  ): Promise<T> {
    const store = this.options.SOURCE_FHIR_STORE_ID;
    if (store === undefined) throw new Error("SOURCE_FHIR_STORE_ID is required");
    const url = `${this.#storeBase(store)}/${encodeURIComponent(resourceType)}/${encodeURIComponent(id)}`;
    return this.#request<T>("read-source", url, runId);
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

  public async persistPackage(
    list: FhirResource,
    documentBundle: FhirBundle,
    runId: string,
    extras: FhirResource[] = [],
  ): Promise<FhirBundle> {
    const store = this.options.TARGET_FHIR_STORE_ID;
    if (store === undefined) throw new Error("TARGET_FHIR_STORE_ID is required");

    const transaction = buildPersistTransaction(list, documentBundle, runId, extras);

    return this.#request<FhirBundle>("execute-bundle", this.#storeBase(store), runId, {
      method: "POST",
      body: JSON.stringify(transaction),
    });
  }
}
