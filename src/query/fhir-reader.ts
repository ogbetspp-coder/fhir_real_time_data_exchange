import { randomUUID } from "node:crypto";

import { GoogleAuth } from "google-auth-library";

import type { FhirBundle, FhirResource } from "../fhir/types.js";
import { sha256 } from "../lib/hash.js";
import type { QueryConfig } from "./config.js";

// Reads of the validated store, and nothing else. The request pattern follows
// src/gcp/healthcare.ts — including its hash-only error style, because a FHIR error body may
// quote content — but the client is deliberately not shared: the query service must not hold the
// worker's store configuration or its credential scope, and it needs three reads, no writes
// (ADR 0004, points 1 and 3).

export type FhirReader = {
  readBundle(bundleId: string): Promise<FhirBundle | undefined>;
  readBundleVersion(bundleId: string, versionId: string): Promise<FhirBundle | undefined>;
  findProvenanceForBundle(bundleId: string): Promise<FhirResource | undefined>;
};

export class FhirReadError extends Error {
  public constructor(message: string) {
    super(message);
    this.name = "FhirReadError";
  }
}

type ReaderOptions = Pick<
  QueryConfig,
  "GOOGLE_CLOUD_PROJECT" | "GCP_LOCATION" | "HEALTHCARE_DATASET_ID" | "TARGET_FHIR_STORE_ID"
>;

function isBundle(resource: FhirResource): resource is FhirBundle {
  return resource.resourceType === "Bundle" && Array.isArray(resource.entry);
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
  async #read<T>(url: string): Promise<T | undefined> {
    const token = await this.#auth.getAccessToken();
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
    });

    if (response.status === 404 || response.status === 410) return undefined;

    const body: unknown = await response.json();
    if (!response.ok) {
      // The body may quote FHIR content; reference it by hash and issue count only.
      const issues = (body as { issue?: unknown }).issue;
      const issueCount = Array.isArray(issues) ? issues.length : 0;
      throw new FhirReadError(
        `Healthcare API ${String(response.status)} ${response.statusText} (response sha256 ${sha256(body)}, ${String(issueCount)} issues)`,
      );
    }
    return body as T;
  }

  public async readBundle(bundleId: string): Promise<FhirBundle | undefined> {
    return this.#read<FhirBundle>(`${this.#storeBase()}/Bundle/${encodeURIComponent(bundleId)}`);
  }

  public async readBundleVersion(
    bundleId: string,
    versionId: string,
  ): Promise<FhirBundle | undefined> {
    const url = `${this.#storeBase()}/Bundle/${encodeURIComponent(bundleId)}/_history/${encodeURIComponent(versionId)}`;
    return this.#read<FhirBundle>(url);
  }

  public async findProvenanceForBundle(bundleId: string): Promise<FhirResource | undefined> {
    const query = new URLSearchParams({ target: `Bundle/${bundleId}`, _count: "1" });
    const searchSet = await this.#read<FhirBundle>(
      `${this.#storeBase()}/Provenance?${query.toString()}`,
    );
    if (searchSet === undefined || !isBundle(searchSet)) return undefined;
    return searchSet.entry.find(({ resource }) => resource.resourceType === "Provenance")?.resource;
  }
}
