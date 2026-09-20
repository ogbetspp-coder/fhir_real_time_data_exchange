import { describe, expect, it, vi } from "vitest";

import { log } from "../src/lib/logger.js";

type LogEntry = Record<string, unknown>;

type Captured = { out: LogEntry[]; err: LogEntry[] };

function record(target: LogEntry[]): (...args: unknown[]) => void {
  return (...args: unknown[]) => {
    const [line] = args;
    if (typeof line !== "string") throw new Error("logger must emit a single JSON string");
    target.push(JSON.parse(line) as LogEntry);
  };
}

function capture(run: () => void): Captured {
  const out: LogEntry[] = [];
  const err: LogEntry[] = [];
  const outSpy = vi.spyOn(console, "log").mockImplementation(record(out));
  const errSpy = vi.spyOn(console, "error").mockImplementation(record(err));
  try {
    run();
  } finally {
    outSpy.mockRestore();
    errSpy.mockRestore();
  }
  return { out, err };
}

function fieldsOf(fields: Record<string, string | number | boolean | undefined>): LogEntry {
  const { out } = capture(() => {
    log("info", "test entry", fields);
  });
  const entry = out[0];
  if (entry === undefined) throw new Error("expected exactly one captured log line");
  expect(out).toHaveLength(1);
  return entry;
}

const forbiddenKeys = [
  "payload",
  "requestPayload",
  "resource",
  "resourceBody",
  "sourceResource",
  "narrative",
  "narrativeDiv",
  "text",
  "sourceText",
  "token",
  "accessToken",
  "secret",
  "credential",
  "credentials",
  "xhtml",
  "span",
  "sourceSpan",
  "excerpt",
  "diff",
  "diffHint",
  "hint",
  "div",
  "content",
  "approvedContent",
  "prompt",
  "promptTemplate",
  "issue",
  "issues",
  "Payload",
  "NARRATIVE",
  "SectionDiv",
];

describe("structured logger redaction", () => {
  it("emits the documented structured envelope on console.log", () => {
    const entry = fieldsOf({ runId: "run-1", stage: "pipeline" });

    expect(entry.severity).toBe("INFO");
    expect(entry.message).toBe("test entry");
    expect(typeof entry.timestamp).toBe("string");
    expect(entry.runId).toBe("run-1");
    expect(entry.stage).toBe("pipeline");
  });

  it("keeps resourceType and resourceId even though they contain a forbidden substring", () => {
    const entry = fieldsOf({ resourceType: "Bundle", resourceId: "bundle-1" });

    expect(entry.resourceType).toBe("Bundle");
    expect(entry.resourceId).toBe("bundle-1");
  });

  it("keeps credentialType while still dropping credential and credentials", () => {
    const entry = fieldsOf({
      credentialType: "access-token",
      credential: "ya29.opaque",
      credentials: "ya29.opaque",
    });

    expect(entry.credentialType).toBe("access-token");
    expect(Object.keys(entry)).not.toContain("credential");
    expect(Object.keys(entry)).not.toContain("credentials");
  });

  it("drops every other forbidden key", () => {
    for (const key of forbiddenKeys) {
      const entry = fieldsOf({ runId: "run-1", [key]: "Synthetic demonstration content" });

      expect(Object.keys(entry)).not.toContain(key);
      expect(entry[key]).toBeUndefined();
      expect(entry.runId).toBe("run-1");
    }
  });

  it("drops string values longer than 512 characters and keeps the boundary length", () => {
    const boundary = "a".repeat(512);
    const entry = fieldsOf({ stage: boundary, outcome: "b".repeat(513) });

    expect(entry.stage).toBe(boundary);
    expect(Object.keys(entry)).not.toContain("outcome");
  });

  it("drops string values containing a markup opening bracket", () => {
    const entry = fieldsOf({
      stage: "pipeline",
      outcome: '<div xmlns="http://www.w3.org/1999/xhtml"><p>Synthetic</p></div>',
      profile: "a < b",
    });

    expect(entry.stage).toBe("pipeline");
    expect(Object.keys(entry)).not.toContain("outcome");
    expect(Object.keys(entry)).not.toContain("profile");
  });

  it("passes non-string values and drops undefined ones", () => {
    const entry = fieldsOf({
      durationMs: 0,
      errorCount: 42,
      warningCount: -1,
      dryRun: true,
      persisted: false,
      stage: undefined,
    });

    expect(entry.durationMs).toBe(0);
    expect(entry.errorCount).toBe(42);
    expect(entry.warningCount).toBe(-1);
    expect(entry.dryRun).toBe(true);
    expect(entry.persisted).toBe(false);
    expect(Object.keys(entry)).not.toContain("stage");
  });

  it("redacts a message that fails the value guard instead of trusting the caller", () => {
    const { out } = capture(() => {
      log("info", '<div xmlns="http://www.w3.org/1999/xhtml"><p>Synthetic</p></div>', {});
      log("info", "x".repeat(513), {});
      log("info", "Run completed", {});
    });

    expect(out.map((entry) => entry.message)).toEqual([
      "[message redacted]",
      "[message redacted]",
      "Run completed",
    ]);
  });

  it("writes error level to console.error and redacts there too", () => {
    const { out, err } = capture(() => {
      log("error", "Request failed", { stage: "http", errorType: "Error", payload: "x" });
    });
    const entry = err[0];
    if (entry === undefined) throw new Error("expected one captured error line");

    expect(out).toHaveLength(0);
    expect(err).toHaveLength(1);
    expect(entry.severity).toBe("ERROR");
    expect(entry.errorType).toBe("Error");
    expect(Object.keys(entry)).not.toContain("payload");
  });
});
