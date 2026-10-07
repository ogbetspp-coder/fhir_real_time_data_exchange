import { serve } from "@hono/node-server";

import { parseApproverMap } from "../approval/approve.js";
import { loadEmaMapping } from "../fhir/mapping.js";
import { log } from "../lib/logger.js";
import { loadSignerConfig } from "./config.js";
import { SIGNER_SERVICE_NAME } from "./service.js";
import { buildSignerApp } from "./wiring.js";

// Entrypoint of the approval signer (Cloud Run: ema-flow-<env>-signer). Its identity holds
// signerVerifier on approval-signing-hsm and nothing else in KMS, create and read on the heads
// bucket, create on `reviews/` and `approvals/` and read on `reviews/` in the evidence bucket, and
// read on the submissions bucket (infra/signer.tf). What it is built from is buildSignerApp's.

const config = loadSignerConfig();
const app = buildSignerApp(config, await loadEmaMapping());

serve({ fetch: app.fetch, port: config.PORT, hostname: "0.0.0.0" }, ({ port }) => {
  log("info", "ema-flow approval signer listening", {
    service: SIGNER_SERVICE_NAME,
    stage: "startup",
    port,
    environment: config.ENVIRONMENT,
    // Counts and switches, never the map's subjects or addresses.
    approverCount: parseApproverMap(config.APPROVER_MAP_JSON).entries.size,
    addOnConfigured: config.ADDON_SERVICE_ACCOUNT !== "" && config.ADDON_OAUTH_CLIENT_ID !== "",
  });
});
