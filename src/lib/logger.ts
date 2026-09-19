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

const forbiddenFieldPattern = /(?:payload|resource|narrative|text|token|secret|credential|xhtml)/i;

function sanitize(fields: LogFields): LogFields {
  return Object.fromEntries(
    Object.entries(fields).filter(
      ([key, value]) => value !== undefined && !forbiddenFieldPattern.test(key),
    ),
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
