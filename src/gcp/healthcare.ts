import { GoogleAuth } from "google-auth-library";

import type { AppConfig } from "../config.js";
import type { BundleEntry, FhirBundle, FhirResource, OperationOutcome } from "../fhir/types.js";

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

  async #request<T>(url: string, runId: string, init: RequestInit = {}): Promise<T> {
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
      const detail = JSON.stringify(body);
      throw new Error(`Healthcare API ${response.status} ${response.statusText}: ${detail}`);
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
    return this.#request<T>(url, runId);
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
    return this.#request<OperationOutcome>(url, runId, {
      method: "POST",
      body: JSON.stringify(resource),
    });
  }

  public async persistPackage(
    list: FhirResource,
    documentBundle: FhirBundle,
    runId: string,
  ): Promise<FhirBundle> {
    const store = this.options.TARGET_FHIR_STORE_ID;
    if (store === undefined) throw new Error("TARGET_FHIR_STORE_ID is required");

    const resources = [
      list,
      ...documentBundle.entry.map(({ resource }) => resource),
      documentBundle,
    ];
    const transactionEntries: BundleEntry[] = resources.map((resource) => {
      if (resource.id === undefined) {
        throw new Error(`${resource.resourceType} requires an id for idempotent persistence`);
      }
      return {
        fullUrl: `urn:uuid:${resource.id}`,
        resource,
        request: {
          method: "PUT",
          url: `${resource.resourceType}/${resource.id}`,
        },
      } as BundleEntry;
    });

    const transaction: FhirBundle = {
      resourceType: "Bundle",
      identifier: {
        system: "https://khs.dev/fhir/identifier/transaction",
        value: runId,
      },
      type: "transaction",
      timestamp: new Date().toISOString(),
      entry: transactionEntries,
    };

    return this.#request<FhirBundle>(this.#storeBase(store), runId, {
      method: "POST",
      body: JSON.stringify(transaction),
    });
  }
}
