import { loadConfig } from "../src/config.js";
import { loadEmaMapping } from "../src/fhir/mapping.js";
import { createSyntheticType2Bundle } from "../src/fixtures/synthetic.js";
import { runPipeline } from "../src/pipeline.js";

const mapping = await loadEmaMapping();
const config = loadConfig({ ...process.env, DRY_RUN: "true", NODE_ENV: "development" });
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
