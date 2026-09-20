import { trace } from "@opentelemetry/api";

type LogLevel = "debug" | "info" | "warning" | "error";

type LogFields = {
  runId?: string;
  stage?: string;
  resourceType?: string;
  resourceId?: string;
  profile?: string;
  outcome?: string;
  durationMs?: number;
  errorCount?: number;
  warningCount?: number;
  [key: string]: string | number | boolean | undefined;
};

const forbiddenFieldPattern =
  /(?:payload|resource|narrative|text|token|secret|credential|xhtml|span|excerpt|diff|hint|div|content|prompt|issue)/i;

// The only two field names allowed to contain a forbidden substring: both carry FHIR metadata,
// never FHIR content.
const allowedFieldNames = new Set(["resourceType", "resourceId"]);

const maxValueLength = 512;

function isSafeValue(value: string | number | boolean): boolean {
  if (typeof value !== "string") return true;
  return value.length <= maxValueLength && !value.includes("<");
}

function sanitize(fields: LogFields): LogFields {
  return Object.fromEntries(
    Object.entries(fields).filter(([key, value]) => {
      if (value === undefined) return false;
      if (!allowedFieldNames.has(key) && forbiddenFieldPattern.test(key)) return false;
      return isSafeValue(value);
    }),
  );
}

export function log(level: LogLevel, message: string, fields: LogFields = {}): void {
  const spanContext = trace.getActiveSpan()?.spanContext();
  const entry: Record<string, unknown> = {
    severity: level.toUpperCase(),
    message,
    timestamp: new Date().toISOString(),
    ...sanitize(fields),
  };

  if (spanContext !== undefined) {
    const projectId = process.env.GOOGLE_CLOUD_PROJECT;
    if (projectId !== undefined) {
      entry["logging.googleapis.com/trace"] = `projects/${projectId}/traces/${spanContext.traceId}`;
    }
    entry["logging.googleapis.com/spanId"] = spanContext.spanId;
    entry["logging.googleapis.com/trace_sampled"] = (spanContext.traceFlags & 0x1) === 0x1;
  }

  const output = JSON.stringify(entry);
  if (level === "error") {
    console.error(output);
  } else {
    console.log(output);
  }
}
