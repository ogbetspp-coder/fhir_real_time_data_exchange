import { Storage } from "@google-cloud/storage";
import { GoogleAuth } from "google-auth-library";

import { loadEmaMapping } from "../../src/fhir/mapping.js";
import {
  buildSeedPlan,
  dryRunLines,
  parseRunResponse,
  runsEndpoint,
  seedRecordLine,
  seedStep,
  type DemoSeedDeps,
} from "./seed-plan.js";

// Seeds the demonstration set (roadmap item 1c) through the real document path: three synthetic
// products at version 1, then the default product at version 2. Each hand-off is written to
// `gs://$SUBMISSION_BUCKET/demo/<productId>/v<version>/` exactly as Zone A would write it, and
// then named by URI and hash in a `document` run request to the deployed worker. Nothing here
// bypasses the ingress gate, the fidelity check, validation, or the evidence chain — the point
// of the demonstration is that the demonstration data went the same way as everything else.
//
// Idempotence: this is a seeding script, not a reconciler. Re-running it re-publishes the same
// content, and because the EMA document Bundle id is stable per product, the FHIR store answers
// with a new version of the same resource each time. That is honest but it inflates the history
// the second scene depends on, so seed once per environment and use `--dry-run` to rehearse.
//
// Usage:
//   SUBMISSION_BUCKET=... WORKER_URL=https://... npx tsx scripts/demo/seed.ts [--dry-run]
// Authentication is a Google-signed ID token for the worker's audience, from Application
// Default Credentials, or `WORKER_ID_TOKEN` when one has already been minted. Whichever it
// comes from, the token's `aud` claim is decoded locally and the script exits before the first
// request when it is not the worker URL.

function required(name: string): string {
  const value = process.env[name]?.trim();
  if (value === undefined || value === "") {
    console.error(`${name} is required`);
    process.exit(1);
  }
  return value;
}

// The `aud` claim of a JWT, decoded locally. Nothing is verified here — the signature is
// Cloud Run's business — and neither the token nor any other claim is read or printed.
function audienceOf(token: string): string[] {
  const payload = token.split(".")[1];
  if (payload === undefined) return [];
  let claims: unknown;
  try {
    claims = JSON.parse(Buffer.from(payload, "base64url").toString("utf8"));
  } catch {
    return [];
  }
  const aud = (claims as { aud?: unknown } | null)?.aud;
  if (typeof aud === "string") return [aud];
  return Array.isArray(aud)
    ? aud.filter((value): value is string => typeof value === "string")
    : [];
}

// Cloud Run checks the ID token's audience at the edge, so a token minted for anything but the
// worker's URL is refused there with a 403 this script cannot explain. Application Default
// Credentials of type `authorized_user` — a human's `gcloud auth application-default login` —
// are the common case: google-auth-library ignores the requested audience for them and returns
// an identity token whose `aud` is the ADC OAuth client id. That is checked here so the script
// says which credential to use instead of failing at the worker.
function requireWorkerAudience(token: string, audience: string, source: string): void {
  if (audienceOf(token).includes(audience)) return;
  console.error(
    [
      `The ${source} is not an ID token for ${audience}, so Cloud Run will refuse it.`,
      "Application Default Credentials of type authorized_user cannot mint one: the audience is",
      "ignored and the token is issued for the ADC OAuth client instead.",
      "Set WORKER_ID_TOKEN to a token minted by a service account that holds",
      "roles/run.invoker on the worker — the deploy creates ema-flow-workflow-<env>, and the",
      "deployer account is NOT one of them — after granting yourself",
      "roles/iam.serviceAccountTokenCreator on it:",
      `  gcloud auth print-identity-token --impersonate-service-account=ema-flow-workflow-<env>@<project>.iam.gserviceaccount.com --audiences=${audience} --include-email`,
      "or run this script as a service account whose ADC is of type external_account or",
      "service_account.",
    ].join("\n"),
  );
  process.exit(1);
}

async function authorization(workerUrl: string): Promise<string> {
  const audience = workerUrl.replace(/\/+$/, "");
  const minted = process.env.WORKER_ID_TOKEN?.trim();
  if (minted !== undefined && minted !== "") {
    requireWorkerAudience(minted, audience, "WORKER_ID_TOKEN");
    return `Bearer ${minted}`;
  }
  const auth = new GoogleAuth();
  const client = await auth.getIdTokenClient(audience);
  const headers = await client.getRequestHeaders();
  const header = headers.get("authorization");
  if (header === null) {
    throw new Error("Application Default Credentials produced no ID token for the worker");
  }
  requireWorkerAudience(
    header.replace(/^Bearer\s+/i, ""),
    audience,
    "ID token from Application Default Credentials",
  );
  return header;
}

async function cloudDeps(bucket: string, workerUrl: string): Promise<DemoSeedDeps> {
  const projectId = process.env.GOOGLE_CLOUD_PROJECT?.trim();
  const storage = new Storage(projectId === undefined || projectId === "" ? {} : { projectId });
  const header = await authorization(workerUrl);
  const endpoint = runsEndpoint(workerUrl);

  return {
    putObject: async (objectName, value) => {
      // Hashes in this system cover the JSON value, never the stored bytes, so indentation is
      // free and a reviewer can read the object in the console.
      await storage
        .bucket(bucket)
        .file(objectName)
        .save(JSON.stringify(value, null, 2), {
          contentType: "application/json",
          resumable: false,
        });
    },
    startRun: async (request) => {
      const response = await fetch(endpoint, {
        method: "POST",
        headers: { "content-type": "application/json", authorization: header },
        body: JSON.stringify(request),
      });
      if (!response.ok) {
        // The body can quote an upstream error; only the status code is printed.
        throw new Error(`Worker refused the run (HTTP ${String(response.status)})`);
      }
      const body: unknown = await response.json();
      return parseRunResponse(body);
    },
  };
}

const bucket = required("SUBMISSION_BUCKET");
const workerUrl = required("WORKER_URL");
const dryRun = process.argv.slice(2).includes("--dry-run");

const mapping = await loadEmaMapping();
const plan = buildSeedPlan(mapping, bucket);

if (dryRun) {
  for (const step of plan) {
    for (const line of dryRunLines(step, workerUrl)) console.log(line);
  }
} else {
  const deps = await cloudDeps(bucket, workerUrl);
  for (const step of plan) {
    console.log(seedRecordLine(await seedStep(step, deps)));
  }
}
