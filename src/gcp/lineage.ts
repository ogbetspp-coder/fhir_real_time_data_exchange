import { LineageClient } from "@google-cloud/lineage";

import type { AppConfig } from "../config.js";

function timestamp(value: string): { seconds: number } {
  return { seconds: Math.floor(new Date(value).getTime() / 1000) };
}

export type LineageRecord = {
  runId: string;
  startedAt: string;
  completedAt: string;
  // The mapping manifest the run used, as `mappingReference` names it (`<id>#<mappingVersion>`),
  // so lineage and the run manifest name the same version.
  mapping: string;
  sourceFqn: string;
  targetFhirFqn: string;
  targetBigQueryFqn: string;
  inputHash: string;
  outputHash: string;
};

export class GcpLineagePublisher {
  readonly #client = new LineageClient();

  public constructor(private readonly config: AppConfig) {}

  public async publish(record: LineageRecord): Promise<string[]> {
    const project = this.config.GOOGLE_CLOUD_PROJECT;
    if (project === undefined) throw new Error("GOOGLE_CLOUD_PROJECT is required");
    const parent = `projects/${project}/locations/${this.config.GCP_LOCATION}`;

    const [process] = await this.#client.createProcess({
      parent,
      requestId: record.runId,
      process: {
        displayName: "HL7 Global ePI Type 2 to EMA EU ePI",
        attributes: {
          mapping: { stringValue: record.mapping },
        },
        origin: {
          sourceType: "CUSTOM",
          name: "ema-flow",
        },
      },
    });
    if (process.name === null || process.name === undefined) {
      throw new Error("Data Lineage API returned no process name");
    }

    const [run] = await this.#client.createRun({
      parent: process.name,
      requestId: record.runId,
      run: {
        displayName: record.runId,
        startTime: timestamp(record.startedAt),
        endTime: timestamp(record.completedAt),
        state: "COMPLETED",
        attributes: {
          inputHash: { stringValue: record.inputHash },
          outputHash: { stringValue: record.outputHash },
        },
      },
    });
    if (run.name === null || run.name === undefined) {
      throw new Error("Data Lineage API returned no run name");
    }

    const [event] = await this.#client.createLineageEvent({
      parent: run.name,
      requestId: record.runId,
      lineageEvent: {
        startTime: timestamp(record.startedAt),
        endTime: timestamp(record.completedAt),
        links: [
          {
            source: { fullyQualifiedName: record.sourceFqn },
            target: { fullyQualifiedName: record.targetFhirFqn },
          },
          {
            source: { fullyQualifiedName: record.targetFhirFqn },
            target: { fullyQualifiedName: record.targetBigQueryFqn },
          },
        ],
      },
    });

    return [process.name, run.name, event.name ?? ""];
  }
}
