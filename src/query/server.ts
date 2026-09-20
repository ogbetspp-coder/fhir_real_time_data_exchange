import { loadEmaMapping } from "../fhir/mapping.js";
import { log } from "../lib/logger.js";
import { createQueryServer } from "./app.js";
import { googleIdTokenVerifier } from "./auth.js";
import { loadQueryConfig } from "./config.js";
import { parseEntitlements } from "./entitlements.js";
import { HealthcareFhirReader } from "./fhir-reader.js";

// Entrypoint of the query service (Cloud Run: ema-flow-<env>-query). It holds no state across
// requests and writes nothing anywhere: its identity can only read the validated FHIR store and
// write logs (ADR 0004).

const config = loadQueryConfig();
const mapping = await loadEmaMapping();

const server = createQueryServer({
  reader: new HealthcareFhirReader(config),
  mapping,
  serviceVersion: config.QUERY_SERVICE_VERSION,
  verifier: googleIdTokenVerifier(config.QUERY_AUDIENCE),
  entitlements: parseEntitlements(config.QUERY_ENTITLEMENTS_JSON),
});

server.listen(config.PORT, "0.0.0.0", () => {
  log("info", "ema-flow query service listening", {
    stage: "startup",
    port: config.PORT,
    serviceVersion: config.QUERY_SERVICE_VERSION,
    imageDigest: config.IMAGE_DIGEST,
  });
});
