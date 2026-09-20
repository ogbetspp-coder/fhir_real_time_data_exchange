import { OAuth2Client } from "google-auth-library";

import { loadEmaMapping } from "../fhir/mapping.js";
import { log } from "../lib/logger.js";
import { QUERY_SERVICE_NAME, createQueryServer } from "./app.js";
import { googleCredentialVerifier } from "./auth.js";
import { loadQueryConfig } from "./config.js";
import { parseEntitlements } from "./entitlements.js";
import { HealthcareFhirReader } from "./fhir-reader.js";

// Entrypoint of the query service (Cloud Run: ema-flow-<env>-query). It holds no state across
// requests other than the access-token verification cache in src/query/auth.ts, and writes
// nothing anywhere: its identity can only read the validated FHIR store and write logs
// (ADR 0004).

const config = loadQueryConfig();
const mapping = await loadEmaMapping();

const server = createQueryServer({
  reader: new HealthcareFhirReader(config),
  mapping,
  serviceVersion: config.QUERY_SERVICE_VERSION,
  imageDigest: config.IMAGE_DIGEST,
  verifier: googleCredentialVerifier({
    audience: config.QUERY_AUDIENCE,
    oauthClientIds: config.QUERY_OAUTH_CLIENT_IDS,
    client: new OAuth2Client(),
  }),
  entitlements: parseEntitlements(config.QUERY_ENTITLEMENTS_JSON),
});

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
