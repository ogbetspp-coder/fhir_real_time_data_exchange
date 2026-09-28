import { spawnSync } from "node:child_process";
import path from "node:path";

import { describe, expect, it } from "vitest";

// `scripts/demo/seed.ts` is a script with a top-level body, so its audience check is exercised
// the way an operator meets it: by running the script. Nothing here reaches Google — the token
// is synthetic and the run is pointed at credentials that do not exist, so the script stops
// before its first request either way.

const TSX = path.resolve("node_modules/.bin/tsx");
const SCRIPT = path.resolve("scripts/demo/seed.ts");
const WORKER_URL = "https://ema-flow-test-worker.invalid";

// header.payload.signature, with only the `aud` claim the script decodes. Not signed, and never
// presented to anything.
function syntheticToken(audience: string): string {
  const part = (value: unknown): string =>
    Buffer.from(JSON.stringify(value), "utf8").toString("base64url");
  return `${part({ alg: "RS256", typ: "JWT" })}.${part({ aud: audience })}.c2lnbmF0dXJl`;
}

type Result = { status: number; stderr: string };

// Starting tsx and loading the Google clients takes a second or two alone and several times that
// on a loaded machine (a concurrent run failed at vitest's default 5 s, audit B15). So the child
// has a bound of its own, under each test's: a run that hangs is killed and reported as such,
// rather than failing as a test timeout that says nothing.
const CHILD_TIMEOUT = 45_000;
const TEST_TIMEOUT = 60_000;

function seed(workerIdToken: string): Result {
  const run = spawnSync(TSX, [SCRIPT], {
    encoding: "utf8",
    timeout: CHILD_TIMEOUT,
    env: {
      ...process.env,
      SUBMISSION_BUCKET: "ema-flow-test-submissions.invalid",
      WORKER_URL,
      WORKER_ID_TOKEN: workerIdToken,
      // No credentials can be loaded, so a run that passes the audience check fails at the
      // first Google client instead of reaching a real project.
      GOOGLE_APPLICATION_CREDENTIALS: path.resolve("test/ci/no-such-credentials.json"),
    },
  });
  if (run.error !== undefined)
    throw new Error(`scripts/demo/seed.ts did not finish: ${run.error.message}`);
  return { status: run.status ?? -1, stderr: run.stderr };
}

const REFUSAL = "Cloud Run will refuse it";

describe("demo seed worker token audience", () => {
  it(
    "stops on a token minted for another audience and names the way to a good one",
    () => {
      const result = seed(syntheticToken("764086051850-6qr4p6gpi6hn506pt8ejuq83di341hur"));

      expect(result.status).toBe(1);
      expect(result.stderr).toContain(`The WORKER_ID_TOKEN is not an ID token for ${WORKER_URL}`);
      expect(result.stderr).toContain(REFUSAL);
      expect(result.stderr).toContain("--impersonate-service-account");
      expect(result.stderr).toContain(`--audiences=${WORKER_URL}`);
    },
    TEST_TIMEOUT,
  );

  it(
    "stops on a bearer that carries no decodable audience at all",
    () => {
      const result = seed("ya29.an-opaque-access-token");

      expect(result.status).toBe(1);
      expect(result.stderr).toContain(REFUSAL);
    },
    TEST_TIMEOUT,
  );

  it(
    "does not stop a token whose audience is the worker URL",
    () => {
      const result = seed(syntheticToken(WORKER_URL));

      expect(result.stderr).not.toContain(REFUSAL);
      // Absence of the refusal proves nothing on its own: a script that failed to start, or
      // exited at one of its `required()` environment checks, also never prints it. So assert the
      // run reached the work — it gets as far as trying to reach Cloud Storage, which is the
      // first thing that happens after the token is accepted.
      expect(result.stderr).not.toContain("is required");
      expect(result.stderr).toMatch(/storage|credential|ENOENT/i);
    },
    TEST_TIMEOUT,
  );
});
