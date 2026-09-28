import { GoogleAuth } from "google-auth-library";

import { AnyRunManifestSchema } from "../../src/contracts/index.js";
import { sha256 } from "../../src/lib/hash.js";

// Reads every signed run manifest in an evidence bucket and checks that each still parses under
// its own version (AnyRunManifestSchema; the frozen versions of src/contracts/run-manifest-frozen.ts)
// and still hashes to the `manifestHash` it was signed over. A one-off, run by hand; not in CI,
// which has no credentials for a bucket (review of #148, part A L5).
//
// Read-only: GET requests to the Cloud Storage JSON API only, listing and downloading objects under
// `runs/*/signed-manifest.json`; it writes nothing anywhere. It prints counts, and for a manifest
// that fails, its object name and the number of issues; never a manifest's content.
//
//   npx tsx scripts/dev/check-evidence-manifests.ts --bucket <project>-ema-flow-dev-evidence
//
// It authenticates with Application Default Credentials, or, where those need a reauthentication a
// script cannot do, with the caller's gcloud token: ACCESS_TOKEN="$(gcloud auth print-access-token)".

const API = "https://storage.googleapis.com/storage/v1/b";

function argument(name: string): string | undefined {
  const at = process.argv.indexOf(name);
  return at === -1 ? undefined : process.argv[at + 1];
}

const bucket = argument("--bucket");
if (bucket === undefined) {
  console.error("usage: check-evidence-manifests.ts --bucket <evidence bucket>");
  process.exit(2);
}

const token =
  process.env.ACCESS_TOKEN ??
  (await new GoogleAuth({
    scopes: ["https://www.googleapis.com/auth/devstorage.read_only"],
  }).getAccessToken()) ??
  undefined;
if (token === undefined) {
  console.error("no access token: set up Application Default Credentials, or ACCESS_TOKEN");
  process.exit(2);
}

async function get(url: string): Promise<Response> {
  const response = await fetch(url, { headers: { authorization: `Bearer ${token ?? ""}` } });
  if (!response.ok) throw new Error(`Cloud Storage answered ${String(response.status)}`);
  return response;
}

const names: string[] = [];
let pageToken: string | undefined;
do {
  const query = new URLSearchParams({ prefix: "runs/", fields: "items(name),nextPageToken" });
  if (pageToken !== undefined) query.set("pageToken", pageToken);
  const page = (await (await get(`${API}/${bucket}/o?${query.toString()}`)).json()) as {
    items?: { name: string }[];
    nextPageToken?: string;
  };
  for (const { name } of page.items ?? []) {
    if (name.endsWith("/signed-manifest.json")) names.push(name);
  }
  pageToken = page.nextPageToken;
} while (pageToken !== undefined);

const byVersion = new Map<string, number>();
const failures: string[] = [];
for (const name of names) {
  const object = `${API}/${bucket}/o/${encodeURIComponent(name)}?alt=media`;
  const signed = (await (await get(object)).json()) as {
    manifest?: unknown;
    manifestHash?: unknown;
  };
  const parsed = AnyRunManifestSchema.safeParse(signed.manifest);
  if (!parsed.success) {
    failures.push(`${name}: ${String(parsed.error.issues.length)} issues`);
    continue;
  }
  if (sha256(signed.manifest) !== signed.manifestHash) {
    failures.push(`${name}: manifestHash does not match the manifest`);
    continue;
  }
  const key = `${parsed.data.schemaVersion} ${parsed.data.status}`;
  byVersion.set(key, (byVersion.get(key) ?? 0) + 1);
}

console.log(`${String(names.length)} signed manifests in gs://${bucket}/runs/`);
for (const [key, count] of [...byVersion].sort()) console.log(`  ${key}: ${String(count)}`);
console.log(`${String(failures.length)} failed`);
for (const failure of failures) console.log(`  ${failure}`);
process.exit(failures.length === 0 ? 0 : 1);
