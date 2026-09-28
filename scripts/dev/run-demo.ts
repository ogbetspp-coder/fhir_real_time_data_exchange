import { loadConfig } from "../../src/config.js";
import { loadEmaMapping } from "../../src/fhir/mapping.js";
import { createSyntheticType2Bundle } from "../../src/fixtures/synthetic.js";
import { runPipeline } from "../../src/pipeline.js";
import { cleanHead, localEnvironment, outsideRepository } from "./local-runtime.js";

const misplaced = outsideRepository();
if (misplaced !== undefined) {
  console.error(`Not running: ${misplaced}.`);
  process.exit(1);
}

const mapping = await loadEmaMapping();
// This shell's environment, without anything in it that would name a deployed commit or image
// as what ran (./local-runtime.ts): the manifest names this checkout's commit when it is clean.
const shell = new Map(
  Object.entries(process.env).filter((entry): entry is [string, string] => entry[1] !== undefined),
);
const config = loadConfig({
  ...localEnvironment(shell, cleanHead()),
  DRY_RUN: "true",
  NODE_ENV: "development",
  ALLOW_SYNTHETIC_SOURCES: "true",
});
const source = createSyntheticType2Bundle(mapping);
const result = await runPipeline(
  {
    source,
    sourceKind: "fixture",
    sourceResource: "fixture:synthetic-type2-smpc",
  },
  mapping,
  config,
);

console.log(
  JSON.stringify(
    {
      runId: result.runId,
      status: result.status,
      inputHash: result.evidence.manifest.transformation.inputHash,
      outputHash: result.evidence.manifest.transformation.outputHash,
      mappingDecisions: result.mappingDecisions.length,
      validation: result.evidence.manifest.validation,
      profiles: result.evidence.manifest.validation.profiles,
    },
    null,
    2,
  ),
);
