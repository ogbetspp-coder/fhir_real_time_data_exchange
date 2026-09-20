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
// Default Credentials, or `WORKER_ID_TOKEN` when one has already been minted.

function required(name: string): string {
  const value = process.env[name]?.trim();
  if (value === undefined || value === "") {
    console.error(`${name} is required`);
    process.exit(1);
  }
  return value;
}

async function authorization(workerUrl: string): Promise<string> {
  const minted = process.env.WORKER_ID_TOKEN?.trim();
  if (minted !== undefined && minted !== "") return `Bearer ${minted}`;
  const auth = new GoogleAuth();
  const client = await auth.getIdTokenClient(workerUrl.replace(/\/+$/, ""));
  const headers = await client.getRequestHeaders();
  const header = headers.get("authorization");
  if (header === null) {
    throw new Error("Application Default Credentials produced no ID token for the worker");
  }
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
