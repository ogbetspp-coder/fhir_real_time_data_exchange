import { sha256Utf8 } from "../lib/hash.js";
import type { FhirResource, OperationOutcome } from "./types.js";

// How long one validation may take. The sidecar loads its packages before it accepts a
// connection (infra/run.tf's startup probe), so this bounds a validation, not a cold start;
// without it a hung sidecar held the request until Cloud Run's 1800 s timeout.
export const OFFICIAL_VALIDATOR_TIMEOUT_MS = 300_000;

// The validator answered with a status that is not success. The body may quote the narrative
// just submitted, so it travels by the hash of its bytes only.
export class OfficialValidatorError extends Error {
  public override readonly name = "OfficialValidatorError";

  public constructor(
    public readonly status: number,
    public readonly responseSha256: string,
  ) {
    super(`Official FHIR validator refused with ${status} (response sha256 ${responseSha256})`);
  }
}

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

    // Read as text first: a non-JSON error page must not become a SyntaxError that loses the
    // status.
    let status: number;
    let ok: boolean;
    let text: string;
    try {
      const response = await fetch(url, {
        method: "POST",
        headers: {
          "content-type": "application/fhir+json",
          accept: "application/fhir+json",
        },
        body: JSON.stringify(resource),
        signal: AbortSignal.timeout(OFFICIAL_VALIDATOR_TIMEOUT_MS),
      });
      ({ status, ok } = response);
      text = await response.text();
    } catch (error) {
      if (error instanceof DOMException && error.name === "TimeoutError") {
        throw new Error("Official FHIR validator request timed out", { cause: error });
      }
      throw error;
    }
    if (!ok) throw new OfficialValidatorError(status, sha256Utf8(text));

    let body: unknown;
    try {
      body = JSON.parse(text);
    } catch {
      body = undefined;
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
