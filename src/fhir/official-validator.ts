import { sha256 } from "../lib/hash.js";
import type { FhirResource, OperationOutcome } from "./types.js";

export class OfficialFhirValidatorClient {
  public constructor(private readonly baseUrl: string) {}

  public async validate(
    resource: FhirResource,
    profiles: string[] = [],
  ): Promise<OperationOutcome> {
    const url = new URL("/validateResource", this.baseUrl);
    for (const profile of profiles) url.searchParams.append("profile", profile);
    url.searchParams.set("resourceIdRule", "REQUIRED");
    url.searchParams.set("bestPractice", "Warning");

    const response = await fetch(url, {
      method: "POST",
      headers: {
        "content-type": "application/fhir+json",
        accept: "application/fhir+json",
      },
      body: JSON.stringify(resource),
    });
    const body: unknown = await response.json();
    if (!response.ok) {
      // The body may quote the narrative just submitted; reference it by hash only.
      throw new Error(
        `Official FHIR validator ${response.status} ${response.statusText} (response sha256 ${sha256(body)})`,
      );
    }
    if (
      body === null ||
      typeof body !== "object" ||
      (body as { resourceType?: unknown }).resourceType !== "OperationOutcome" ||
      !Array.isArray((body as { issue?: unknown }).issue)
    ) {
      throw new Error("Official FHIR validator returned an invalid OperationOutcome");
    }
    return body as OperationOutcome;
  }
}
