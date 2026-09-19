import { serve } from "@hono/node-server";

import { createApp } from "./app.js";
import { loadConfig } from "./config.js";
import { log } from "./lib/logger.js";

const config = loadConfig();
const app = createApp();

serve(
  {
    fetch: app.fetch,
    port: config.PORT,
    hostname: "0.0.0.0",
  },
  ({ port }) => {
    log("info", "ema-flow service listening", {
      stage: "startup",
      port,
      dryRun: config.DRY_RUN,
    });
  },
);
