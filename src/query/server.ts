import { loadEmaMapping } from "../fhir/mapping.js";
import { log } from "../lib/logger.js";
import { QUERY_SERVICE_NAME, buildQueryServer } from "./app.js";
import { loadQueryConfig } from "./config.js";

// Entrypoint of the query service (Cloud Run: ema-flow-<env>-query). It holds no state across
// requests other than the access-token verification cache in src/query/auth.ts, and writes
// nothing anywhere: its identity can only read the validated FHIR store and write logs
// (ADR 0004). What it is built from is buildQueryServer's, in src/query/app.ts, where a test
// holds the wiring to the configuration.

const config = loadQueryConfig();
const mapping = await loadEmaMapping();

const server = buildQueryServer(config, mapping);

server.listen(config.PORT, "0.0.0.0", () => {
  log("info", "ema-flow query service listening", {
    service: QUERY_SERVICE_NAME,
    stage: "startup",
    port: config.PORT,
    serviceVersion: config.QUERY_SERVICE_VERSION,
    imageDigest: config.IMAGE_DIGEST,
    // A count, not the ids: whether the access-token path is open at all is operational fact.
    oauthClientIdCount: config.QUERY_OAUTH_CLIENT_IDS.length,
  });
});
