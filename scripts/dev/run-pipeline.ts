import { execFileSync } from "node:child_process";

import { loadConfig } from "../../src/config.js";
import { loadEmaMapping } from "../../src/fhir/mapping.js";
import { createSyntheticSubmission } from "../../src/fixtures/synthetic-submission.js";
import { createSyntheticType2Bundle } from "../../src/fixtures/synthetic.js";
import { runPipeline, type PipelineInput } from "../../src/pipeline.js";
import {
  SMOKE_PRODUCT_ID,
  SYNTHETIC_PRODUCT_IDS,
  SYNTHETIC_VERSIONS,
  type SyntheticProductId,
  type SyntheticVersion,
} from "../../src/fixtures/synthetic-products.js";

// Runs the whole pipeline on this machine, against the deployed environment's real Google Cloud
// resources, without deploying anything.
//
// Why this exists: four separate defects were found on 2026-09-20/21, each hidden behind the one
// before it, and each cost a twelve-minute deploy to reveal — official validation, then a
// malformed transaction fullUrl, then a KMS key that was not a key version, then a BigQuery
// schema too wide to create. Every one of them was reachable from a laptop. A run here takes
// about a minute.
//
// The configuration is read from the DEPLOYED worker rather than rebuilt from Terraform outputs,
// because the point is to reproduce what Cloud Run actually runs. Anything rebuilt can drift
// from it, and a local run that passes against a slightly different configuration is worse than
// no local run at all.
//
// Usage:
//   bash scripts/dev/validator-server.sh            # in another terminal, leave it running
//   npx tsx scripts/dev/run-pipeline.ts             # dry run, writes nothing
//   npx tsx scripts/dev/run-pipeline.ts --persist   # writes to the real store, bucket and ledger
//   npx tsx scripts/dev/run-pipeline.ts --source document --product demoxetine --version 2
//
// --persist is deliberately not the default. A run that persists writes a document to the
// validated store, artefacts to the evidence bucket and a row to the ledger, all under the
// deployed environment's identity. That is exactly what makes this useful and exactly why it
// should be asked for.
//
// Be clear about what a dry run is worth: DRY_RUN gates the validators as well as the writes, so
// without --persist this exercises the transform and the two structural preflights and nothing
// else — the answer reports `officialValidationExecuted: false`. It will catch a mapping or
// fixture regression. It will not catch anything the official validator, the Cloud Healthcare
// API, KMS, Cloud Storage or BigQuery would have refused, which is to say it would have caught
// none of the four defects this script was written for. Use --persist to mean it.

type Args = {
  service: string;
  region: string;
  project: string | undefined;
  validatorUrl: string;
  persist: boolean;
  source: "fixture" | "document";
  product: SyntheticProductId;
  version: SyntheticVersion;
};

function parseArgs(argv: string[]): Args {
  const args: Args = {
    service: "ema-flow-dev-worker",
    region: "europe-west4",
    project: undefined,
    validatorUrl: "http://localhost:8090",
    persist: false,
    source: "fixture",
    // The smoke product, like the worker's own fixture path: of the four it is the one a
    // --persist run can overwrite without touching a label the demonstration is about.
    product: SMOKE_PRODUCT_ID,
    version: SYNTHETIC_VERSIONS[0] ?? 1,
  };
  for (let index = 0; index < argv.length; index += 1) {
    const flag = argv[index];
    const value = (): string => {
      const next = argv[index + 1];
      if (next === undefined) throw new Error(`${String(flag)} needs a value`);
      index += 1;
      return next;
    };
    if (flag === "--persist") args.persist = true;
    else if (flag === "--service") args.service = value();
    else if (flag === "--region") args.region = value();
    else if (flag === "--project") args.project = value();
    else if (flag === "--validator-url") args.validatorUrl = value();
    else if (flag === "--source") {
      const source = value();
      if (source !== "fixture" && source !== "document") {
        throw new Error(`--source must be fixture or document, not ${source}`);
      }
      args.source = source;
    } else if (flag === "--product") {
      const product = value();
      if (!(SYNTHETIC_PRODUCT_IDS as readonly string[]).includes(product)) {
        throw new Error(`--product must be one of ${SYNTHETIC_PRODUCT_IDS.join(", ")}`);
      }
      args.product = product as SyntheticProductId;
    } else if (flag === "--version") {
      const version = Number(value());
      if (!(SYNTHETIC_VERSIONS as readonly number[]).includes(version)) {
        throw new Error(`--version must be one of ${SYNTHETIC_VERSIONS.join(", ")}`);
      }
      args.version = version as SyntheticVersion;
    } else throw new Error(`Unknown argument ${String(flag)}`);
  }
  return args;
}

// Every name/value pair the service describes, whatever the shape of the response: Cloud Run's
// v1 and v2 representations nest the container differently and this is a development tool, not
// a place to encode that difference.
function environmentOf(described: unknown): Map<string, string> {
  const found = new Map<string, string>();
  const walk = (node: unknown): void => {
    if (Array.isArray(node)) {
      node.forEach(walk);
      return;
    }
    if (node === null || typeof node !== "object") return;
    const record = node as Record<string, unknown>;
    if (typeof record.name === "string" && typeof record.value === "string") {
      if (/^[A-Z][A-Z0-9_]*$/.test(record.name)) found.set(record.name, record.value);
    }
    Object.values(record).forEach(walk);
  };
  walk(described);
  return found;
}

const args = parseArgs(process.argv.slice(2));

const describeArgs = [
  "--quiet",
  "run",
  "services",
  "describe",
  args.service,
  `--region=${args.region}`,
  "--format=json",
  ...(args.project === undefined ? [] : [`--project=${args.project}`]),
];
let described: unknown;
try {
  described = JSON.parse(execFileSync("gcloud", describeArgs, { encoding: "utf8" }));
} catch {
  console.error(
    `Could not describe Cloud Run service ${args.service} in ${args.region}. Is gcloud on PATH and authenticated?`,
  );
  process.exit(1);
}

const deployed = environmentOf(described);
if (deployed.size === 0) {
  console.error(`No environment variables found on ${args.service}; nothing to run with.`);
  process.exit(1);
}

// The validator is the one thing that is not the deployed value: the sidecar's URL is inside
// Cloud Run's network. DRY_RUN is forced on unless --persist, and is set last so a deployed
// DRY_RUN cannot quietly re-enable writing.
const environment: Record<string, string> = Object.fromEntries(deployed);
environment.FHIR_VALIDATOR_URL = args.validatorUrl;
environment.DRY_RUN = args.persist ? "false" : "true";

const config = loadConfig(environment);
const mapping = await loadEmaMapping();
const runId = crypto.randomUUID();

let input: PipelineInput;
if (args.source === "fixture") {
  input = {
    runId,
    sourceKind: "fixture",
    source: createSyntheticType2Bundle(mapping, { product: args.product, version: args.version }),
    sourceResource: "fixture:synthetic-type2-smpc",
  };
} else {
  const { submission, fidelityReport, sourceText } = createSyntheticSubmission(mapping, {
    product: args.product,
    version: args.version,
  });
  input = {
    runId,
    sourceKind: "document",
    submission,
    fidelityReport,
    sourceText,
    sourceResource: `document:${submission.provenance.sourceDocument.sha256}`,
  };
}

console.log(
  `${args.persist ? "PERSISTING" : "dry run"}: ${args.source} ${args.product} v${String(args.version)} against ${String(environment.GOOGLE_CLOUD_PROJECT)}/${String(environment.TARGET_FHIR_STORE_ID)}`,
);
console.log(`runId ${runId}`);

try {
  const result = await runPipeline(input, mapping, config);
  // Closed fields only, the same ones the worker's HTTP surface answers with.
  console.log(
    JSON.stringify(
      {
        status: result.status,
        manifestHash: result.evidence.manifestHash,
        signed: result.evidence.signature !== undefined,
        targetBundleId: result.emaBundle.id,
        mappingDecisions: result.mappingDecisions.length,
        validation: result.evidence.manifest.validation,
        artifacts: result.artifactUris.length,
      },
      null,
      2,
    ),
  );
} catch (error) {
  // The name is the useful part: an upstream refusal carries its own type (HealthcareApiError),
  // which is what tells an operator which gate refused without reading Cloud Audit Logs.
  const failure = error instanceof Error ? error : new Error("unknown");
  console.error(`FAILED ${failure.name}: ${failure.message}`);
  process.exit(1);
}
