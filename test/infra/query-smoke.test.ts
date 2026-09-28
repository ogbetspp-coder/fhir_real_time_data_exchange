import { spawn } from "node:child_process";
import { readFileSync } from "node:fs";
import { mkdtemp, readFile, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";

import { describe, expect, it } from "vitest";

// The query service's post-deploy smoke (scripts/gcp/deploy.sh query-smoke). Run for real here,
// against stand-ins for terraform, gcloud and curl on PATH, so what is asserted is the phase's
// verdict on each answer the deployed service could give, not the wording of the script.

const script = readFileSync("scripts/gcp/deploy.sh", "utf8");
const workflow = readFileSync(".github/workflows/deploy.yml", "utf8");

const QUERY_URL = "https://ema-flow-dev-query-123456789012.europe-west4.run.app";
const COMMIT = "0123456789abcdef0123456789abcdef01234567";
const DEV_PROJECT =
  /^EXPECTED_PROJECT_ID=(\S+)$/m.exec(
    readFileSync("scripts/gcp/environments/dev.env", "utf8"),
  )?.[1] ?? "";
const EDGE_TOKEN = "edge.id.token-never-printed";

const TERRAFORM = `#!/bin/sh
case "$*" in
  *"output -raw query_service_url"*) printf '%s' "$STUB_QUERY_URL" ;;
  *) echo "unexpected terraform $*" >&2; exit 2 ;;
esac
`;

const GCLOUD = `#!/bin/sh
echo "gcloud $*" >> "$STUB_DIR/calls.log"
case "$*" in
  *"run services describe"*) cat "$STUB_DIR/describe.json"; exit "\${STUB_DESCRIBE_EXIT:-0}" ;;
  *) echo "unexpected gcloud $*" >&2; exit 2 ;;
esac
`;

// Answers each (caller, path) from a space-separated list of codes, one per call (the last
// repeats). A request carrying X-Serverless-Authorization with the expected token is an "edge"
// caller; anything else is anonymous. Every argument is logged so a test can see what was sent;
// a header read from a file (--header @<file>) is logged as [@file:<the header>], so a test can
// tell a token in the arguments from one in a file, which only the curl process reads.
const CURL = `#!/usr/bin/env bash
printf 'curl' >> "$STUB_DIR/calls.log"
out=""; url=""; caller=anonymous
while [ $# -gt 0 ]; do
  case "$1" in
    --output) out="$2"; printf ' [%s] [%s]' "$1" "$2" >> "$STUB_DIR/calls.log"; shift 2 ;;
    --header)
      header="$2"
      if [ "\${header#@}" != "$header" ]; then header="@file:$(cat "\${header#@}")"; fi
      printf ' [%s] [%s]' "$1" "$header" >> "$STUB_DIR/calls.log"
      [ "$header" = "@file:X-Serverless-Authorization: Bearer $STUB_EDGE_TOKEN" ] && caller=edge
      shift 2 ;;
    --write-out|--max-time|--request|--data)
      printf ' [%s] [%s]' "$1" "$2" >> "$STUB_DIR/calls.log"; shift 2 ;;
    --*) printf ' [%s]' "$1" >> "$STUB_DIR/calls.log"; shift ;;
    *) url="$1"; printf ' [%s]' "$1" >> "$STUB_DIR/calls.log"; shift ;;
  esac
done
echo >> "$STUB_DIR/calls.log"
case "$caller:$url" in
  anonymous:*/mcp) key=mcp; codes="$STUB_MCP_CODES"; body="$STUB_MCP_BODY" ;;
  anonymous:*/readyz) key=readyz; codes="$STUB_READYZ_CODES"; body="$STUB_READYZ_BODY" ;;
  edge:*/mcp) key=edge-mcp; codes="$STUB_EDGE_MCP_CODES"; body="$STUB_EDGE_MCP_BODY" ;;
  edge:*/readyz) key=edge-readyz; codes="$STUB_EDGE_READYZ_CODES"; body="$STUB_EDGE_READYZ_BODY" ;;
  *) echo "unexpected url $url" >&2; exit 2 ;;
esac
n=$(cat "$STUB_DIR/$key.n" 2>/dev/null || echo 0); n=$((n + 1)); echo "$n" > "$STUB_DIR/$key.n"
code=$(echo "$codes" | tr ' ' '\\n' | sed -n "\${n}p")
[ -n "$code" ] || code=$(echo "$codes" | tr ' ' '\\n' | tail -n 1)
printf '%s' "$body" > "$out"
printf '%s' "$code"
`;

const EDGE_403 =
  "<html><head><title>403 Forbidden</title></head><body>Your client does not have permission</body></html>";
const EDGE_401 =
  "<html><head><title>401 Unauthorized</title></head><body>Unauthorized</body></html>";
const SERVICE_401 = JSON.stringify({ error: "unauthenticated" });
const READY = JSON.stringify({ status: "ok", service: "ema-flow-query", version: COMMIT });

function describeJson(overrides: Record<string, unknown> = {}): string {
  return JSON.stringify({
    metadata: { name: "ema-flow-dev-query" },
    status: {
      conditions: [{ type: "Ready", status: "True" }],
      latestCreatedRevisionName: "ema-flow-dev-query-00042-abc",
      latestReadyRevisionName: "ema-flow-dev-query-00042-abc",
      ...overrides,
    },
  });
}

type Answers = {
  mcp?: string;
  mcpBody?: string;
  readyz?: string;
  readyzBody?: string;
  edgeMcp?: string;
  edgeMcpBody?: string;
  edgeReadyz?: string;
  edgeReadyzBody?: string;
  url?: string;
  describe?: string;
  describeExit?: string;
  // The token the phase is given; `null` runs it without one.
  token?: string | null;
};

// One run of the phase in a directory of its own, so runs can go concurrently: each is a few
// hundred milliseconds of bash and python, and there are thirty of them.
async function run(answers: Answers = {}) {
  const stubs = await mkdtemp(join(tmpdir(), "query-smoke-"));
  try {
    for (const [name, text] of [
      ["terraform", TERRAFORM],
      ["gcloud", GCLOUD],
      ["curl", CURL],
    ] as const) {
      await writeFile(join(stubs, name), text, { mode: 0o755 });
    }
    await writeFile(join(stubs, "describe.json"), answers.describe ?? describeJson());

    const token = answers.token === undefined ? EDGE_TOKEN : answers.token;
    const child = spawn("bash", ["scripts/gcp/deploy.sh", "query-smoke"], {
      env: {
        PATH: `${stubs}:${process.env.PATH ?? ""}`,
        HOME: process.env.HOME ?? stubs,
        // dev's own project (scripts/gcp/environments/dev.env): deploy.sh refuses any other.
        GOOGLE_CLOUD_PROJECT: DEV_PROJECT,
        // No repository here, so GITHUB_SHA names the commit (deploy.sh, deploy_provenance).
        GIT_DIR: join(stubs, "no-repository"),
        GCP_REGION: "europe-west4",
        EMA_FLOW_ENVIRONMENT: "dev",
        GITHUB_SHA: COMMIT,
        QUERY_SMOKE_RETRY_SECONDS: "0",
        ...(token === null ? {} : { QUERY_EDGE_ID_TOKEN: token }),
        STUB_DIR: stubs,
        STUB_EDGE_TOKEN: EDGE_TOKEN,
        STUB_QUERY_URL: answers.url ?? QUERY_URL,
        STUB_MCP_CODES: answers.mcp ?? "403",
        STUB_MCP_BODY: answers.mcpBody ?? EDGE_403,
        STUB_READYZ_CODES: answers.readyz ?? "403",
        STUB_READYZ_BODY: answers.readyzBody ?? EDGE_403,
        STUB_EDGE_MCP_CODES: answers.edgeMcp ?? "401",
        STUB_EDGE_MCP_BODY: answers.edgeMcpBody ?? SERVICE_401,
        STUB_EDGE_READYZ_CODES: answers.edgeReadyz ?? "200",
        STUB_EDGE_READYZ_BODY: answers.edgeReadyzBody ?? READY,
        STUB_DESCRIBE_EXIT: answers.describeExit ?? "0",
      },
    });
    let output = "";
    child.stdout.on("data", (chunk: Buffer) => (output += chunk.toString("utf8")));
    child.stderr.on("data", (chunk: Buffer) => (output += chunk.toString("utf8")));
    const status = await new Promise<number | null>((resolve) => {
      child.on("close", resolve);
    });

    let calls: string[] = [];
    try {
      calls = (await readFile(join(stubs, "calls.log"), "utf8")).trim().split("\n");
    } catch {
      // Nothing was called.
    }
    const curls = calls.filter((line) => line.startsWith("curl"));
    return {
      status,
      output,
      calls,
      curls,
      anonymous: curls.filter((line) => !line.includes("X-Serverless-Authorization")),
      edge: curls.filter((line) => line.includes("X-Serverless-Authorization")),
    };
  } finally {
    await rm(stubs, { recursive: true, force: true });
  }
}

// Each run starts bash and a few python3 processes; under a full parallel suite one can take
// seconds, so these carry a timeout of their own rather than the 5s default.
describe.concurrent("the query service smoke", { timeout: 30_000 }, () => {
  it("passes on a Ready, current service walled at the edge and in the container", async () => {
    const { status, output, calls, anonymous, edge } = await run();

    expect(status).toBe(0);
    expect(calls[0]).toBe(
      "gcloud --quiet run services describe ema-flow-dev-query --region=europe-west4 --format=json",
    );
    const request = (method: string, path: string) =>
      expect.stringMatching(
        new RegExp(
          `\\[--request\\] \\[${method}\\] .*\\[${QUERY_URL.replaceAll(".", "\\.")}${path}\\]$`,
        ),
      ) as unknown;
    expect(anonymous).toEqual([request("POST", "/mcp"), request("GET", "/readyz")]);
    expect(edge).toEqual([request("POST", "/mcp"), request("GET", "/readyz")]);
    expect(output).toContain("query service Ready, serving ema-flow-dev-query-00042-abc");
    expect(output).toContain("anonymous POST /mcp: HTTP 403 error=- ");
    expect(output).toContain("anonymous GET /readyz: HTTP 403 error=- ");
    expect(output).toContain(
      "POST /mcp past the edge, no Bearer: HTTP 401 error=unauthenticated status=- service=- version=-",
    );
    expect(output).toContain(
      `GET /readyz past the edge: HTTP 200 error=- status=ok service=ema-flow-query version=${COMMIT}`,
    );
  });

  it("sends no credential anonymously, never an Authorization header, and never prints the token", async () => {
    const { anonymous, edge, output } = await run();
    for (const call of anonymous) expect(call.toLowerCase()).not.toContain("bearer");
    for (const call of [...anonymous, ...edge]) {
      expect(call).not.toMatch(/\[authorization:/i);
    }
    for (const call of edge) {
      // From a file (audit B08, D-7): an argument is readable by every process on the host.
      expect(call).toContain(`[@file:X-Serverless-Authorization: Bearer ${EDGE_TOKEN}]`);
      expect(call).not.toContain(`[X-Serverless-Authorization: Bearer ${EDGE_TOKEN}]`);
    }
    expect(output).not.toContain(EDGE_TOKEN);
  });

  it("runs only the anonymous checks, with a notice, when it has no edge token", async () => {
    const { status, output, edge, anonymous } = await run({ token: null });
    expect(status).toBe(0);
    expect(anonymous).toHaveLength(2);
    expect(edge).toEqual([]);
    expect(output).toContain("QUERY_EDGE_ID_TOKEN is not set");
  });

  it("accepts the container's own 401 anonymously too, printing only closed fields", async () => {
    const { status, output } = await run({
      mcp: "401",
      mcpBody: JSON.stringify({
        error: "unauthenticated",
        detail: "a sentence that must not print",
      }),
    });
    expect(status).toBe(0);
    expect(output).toContain("anonymous POST /mcp: HTTP 401 error=unauthenticated");
    expect(output).not.toContain("a sentence that must not print");
    expect(output).not.toContain("Your client does not have permission");
  });

  describe("anonymously", () => {
    it.each(["200", "202", "204"])("fails when /mcp answers %s", async (code) => {
      const { status, output } = await run({ mcp: code, mcpBody: "{}" });
      expect(status).not.toBe(0);
      expect(output).toContain(
        `anonymous POST /mcp answered ${code} to a caller with no credential`,
      );
    });

    it("fails when /readyz is public", async () => {
      const { status, output } = await run({ readyz: "200", readyzBody: READY });
      expect(status).not.toBe(0);
      expect(output).toContain("anonymous GET /readyz answered 200 to a caller with no credential");
    });

    it("fails when no service answers at the URL", async () => {
      const { status, output } = await run({ mcp: "404" });
      expect(status).not.toBe(0);
      expect(output).toContain(
        `anonymous POST /mcp answered 404: no service answers at ${QUERY_URL}`,
      );
    });

    it("retries a request that got no answer or a frontend 503, and then judges the answer", async () => {
      const { status, anonymous, output } = await run({ mcp: "000 503 403" });
      expect(status).toBe(0);
      expect(anonymous.filter((call) => call.includes("/mcp]"))).toHaveLength(3);
      expect(output).toContain("anonymous POST /mcp: HTTP 403");
    });

    it("gives up after three attempts with no answer", async () => {
      const { status, anonymous } = await run({ mcp: "000" });
      expect(status).not.toBe(0);
      expect(anonymous.filter((call) => call.includes("/mcp]"))).toHaveLength(3);
    });

    it("does not retry a 500, which is an answer", async () => {
      const { status, anonymous } = await run({ mcp: "500" });
      expect(status).not.toBe(0);
      expect(anonymous.filter((call) => call.includes("/mcp]"))).toHaveLength(1);
    });
  });

  describe("past the edge", () => {
    it.each([
      ["401", EDGE_401],
      ["403", EDGE_403],
    ])(
      "fails when the edge refuses the token with %s, so the container was never reached",
      async (code, body) => {
        const { status, output } = await run({ edgeMcp: code, edgeMcpBody: body });
        expect(status).not.toBe(0);
        expect(output).toContain("not the service's own 401 unauthenticated");
      },
    );

    it("fails when the service admits a caller with no Bearer", async () => {
      const { status, output } = await run({ edgeMcp: "200", edgeMcpBody: '{"jsonrpc":"2.0"}' });
      expect(status).not.toBe(0);
      expect(output).toContain(
        "POST /mcp with no Bearer, past Cloud Run's edge, answered HTTP 200",
      );
    });

    it("fails when the revision answering is not this commit's", async () => {
      const stale = JSON.stringify({
        status: "ok",
        service: "ema-flow-query",
        version: "f".repeat(40),
      });
      const { status, output } = await run({ edgeReadyzBody: stale });
      expect(status).not.toBe(0);
      expect(output).toContain(`not 200 from ema-flow-query at version ${COMMIT}`);
    });

    it("fails when something other than the query service answers /readyz", async () => {
      const other = JSON.stringify({ status: "ok", service: "ema-flow-worker", version: COMMIT });
      const { status } = await run({ edgeReadyzBody: other });
      expect(status).not.toBe(0);
    });

    it("fails when /readyz is not 200", async () => {
      const { status } = await run({ edgeReadyz: "500", edgeReadyzBody: "{}" });
      expect(status).not.toBe(0);
    });

    it("retries a cold start's 503", async () => {
      const { status, edge } = await run({ edgeMcp: "503 401" });
      expect(status).toBe(0);
      expect(edge.filter((call) => call.includes("/mcp]"))).toHaveLength(2);
    });
  });

  describe("before any request", () => {
    it("fails, and calls nothing, when Cloud Run does not report the service Ready", async () => {
      const { status, output, curls } = await run({
        describe: describeJson({ conditions: [{ type: "Ready", status: "False" }] }),
      });
      expect(status).not.toBe(0);
      expect(output).toContain("does not report the query service Ready");
      expect(curls).toEqual([]);
    });

    it("fails when this deploy's revision is not the one serving", async () => {
      const { status, output, curls } = await run({
        describe: describeJson({ latestCreatedRevisionName: "ema-flow-dev-query-00043-def" }),
      });
      expect(status).not.toBe(0);
      expect(output).toContain("this deploy's revision did not start");
      expect(curls).toEqual([]);
    });

    it("fails when the service cannot be described", async () => {
      const { status, output, curls } = await run({ describeExit: "1" });
      expect(status).not.toBe(0);
      expect(output).toContain("could not describe Cloud Run service ema-flow-dev-query");
      expect(curls).toEqual([]);
    });

    it("fails when Terraform names no query service URL", async () => {
      const { status, output, calls } = await run({ url: "" });
      expect(status).not.toBe(0);
      expect(output).toContain("terraform output query_service_url was empty");
      expect(calls).toEqual([]);
    });
  });
});

describe("where the query smoke runs", () => {
  it("is a phase of its own, run after the worker smoke in a full deploy", () => {
    expect(script).toMatch(/^ {2}query-smoke\) phase_query_smoke ;;$/m);
    const all = /^ {2}all\)\n([\s\S]*?)\n {4};;/m.exec(script)?.[1] ?? "";
    const phases = all.split("\n").map((line) => line.trim());
    expect(phases.indexOf("phase_smoke")).toBeGreaterThan(0);
    expect(phases.indexOf("phase_query_smoke")).toBeGreaterThan(phases.indexOf("phase_smoke"));
  });

  it("runs in every deploy, after the worker smoke and before failures are closed", () => {
    const worker = workflow.indexOf("- name: Smoke run through the deployed worker");
    const mint = workflow.indexOf("- name: Mint an ID token for the query service's edge");
    const query = workflow.indexOf("- name: Smoke the deployed query service");
    const close = workflow.indexOf("- name: Close superseded failure issues");
    expect(worker).toBeGreaterThan(0);
    expect(mint).toBeGreaterThan(worker);
    expect(query).toBeGreaterThan(mint);
    expect(close).toBeGreaterThan(query);
    const step = workflow.slice(query, close);
    expect(step).toContain(
      "run: bash scripts/gcp/deploy.sh query-smoke 2>&1 | tee -a deploy-run.log",
    );
    expect(step).toContain("QUERY_EDGE_ID_TOKEN: ${{ steps.query_token.outputs.id_token }}");
    // The worker's token is for the worker; nothing else reaches this step.
    expect(step).not.toContain("worker_token");
  });

  it("mints the edge token for the query service's own URL, with the e-mail claim Cloud Run matches", () => {
    const mint = workflow.slice(
      workflow.indexOf("- name: Mint an ID token for the query service's edge"),
      workflow.indexOf("- name: Smoke the deployed query service"),
    );
    expect(mint).toContain("id: query_token");
    expect(mint).toContain("token_format: id_token");
    expect(mint).toContain("id_token_audience: ${{ steps.query.outputs.uri }}");
    expect(mint).toContain("id_token_include_email: true");
    expect(mint).toContain("create_credentials_file: false");
    expect(workflow).toMatch(
      /id: query\n\s+run: \|\n\s+uri="\$\(terraform -chdir=infra output -raw query_service_url\)"/,
    );
  });
});
