import { spawnSync } from "node:child_process";
import { mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";

import { afterEach, describe, expect, it } from "vitest";

// The dashboard drift check (audit I-6). Terraform ignores the dashboard's JSON text, because the
// Monitoring API rewrites it and every plan showed a change; this check is what still notices a
// dashboard that no longer matches its configuration. It must tolerate what the API adds and
// drops, and must catch any configured value that is missing or changed.

const script = path.resolve("scripts/ci/dashboard-drift.py");
const dirs: string[] = [];

afterEach(() => {
  for (const dir of dirs.splice(0)) rmSync(dir, { recursive: true, force: true });
});

function compare(configured: unknown, live: unknown): { status: number | null; out: string } {
  const dir = mkdtempSync(path.join(tmpdir(), "dashboard-drift-"));
  dirs.push(dir);
  const want = path.join(dir, "configured.json");
  const have = path.join(dir, "live.json");
  writeFileSync(want, JSON.stringify(configured));
  writeFileSync(have, typeof live === "string" ? live : JSON.stringify(live));
  const run = spawnSync("python3", [script, want, have], { encoding: "utf8" });
  return { status: run.status, out: run.stdout };
}

const configured = {
  displayName: "EMA Flow (dev)",
  mosaicLayout: {
    columns: 12,
    tiles: [
      {
        xPos: 0,
        yPos: 0,
        width: 6,
        height: 4,
        widget: {
          title: "Completed runs",
          xyChart: { dataSets: [{ plotType: "LINE" }], yAxis: { scale: "LINEAR" } },
        },
      },
      { xPos: 6, yPos: 0, width: 6, height: 4, widget: { title: "Failed runs" } },
    ],
  },
};

describe("the dashboard drift check", () => {
  it("accepts the API's form: defaults added, zero values dropped, numbers as strings", () => {
    const live = {
      name: "projects/1/dashboards/abc",
      etag: "e1",
      displayName: "EMA Flow (dev)",
      mosaicLayout: {
        columns: "12",
        tiles: [
          {
            width: 6,
            height: 4,
            widget: {
              title: "Completed runs",
              xyChart: {
                chartOptions: { mode: "COLOR" },
                dataSets: [{ plotType: "LINE", targetAxis: "Y1" }],
                timeshiftDuration: "0s",
                yAxis: { scale: "LINEAR" },
              },
            },
          },
          { xPos: 6, width: 6, height: 4, widget: { title: "Failed runs" } },
        ],
      },
    };
    expect(compare(configured, live).status).toBe(0);
  });

  it("reports a changed value, a missing value, a missing tile and a moved tile, by path only", () => {
    const changed = structuredClone(configured);
    const tile = changed.mosaicLayout.tiles[1];
    if (tile === undefined) throw new Error("fixture");
    tile.widget.title = "Something else";
    const result = compare(configured, changed);
    expect(result.status).toBe(1);
    expect(result.out).toContain("$.mosaicLayout.tiles[1].widget.title");
    expect(result.out).not.toContain("Something else");

    const missing = structuredClone(configured) as Record<string, unknown>;
    delete missing.displayName;
    expect(compare(configured, missing).out).toContain("$.displayName");

    const fewer = structuredClone(configured);
    fewer.mosaicLayout.tiles.pop();
    expect(compare(configured, fewer).out).toContain("$.mosaicLayout.tiles");

    const moved = structuredClone(configured);
    const first = moved.mosaicLayout.tiles[0];
    if (first === undefined) throw new Error("fixture");
    first.xPos = 3;
    expect(compare(configured, moved).out).toContain("$.mosaicLayout.tiles[0].xPos");
  });

  it("fails as unreadable, not as matching, when an input is not JSON", () => {
    expect(compare(configured, "<html>").status).toBe(2);
  });

  it("compares the configuration Terraform deploys", () => {
    // The dashboard's JSON is ignored by Terraform and published as an output for this check.
    const observability = readFileSync("infra/observability.tf", "utf8");
    expect(observability).toMatch(
      /resource "google_monitoring_dashboard" "operations" \{\n\s+dashboard_json = jsonencode\(local\.operations_dashboard\)\n\n\s+lifecycle \{\n\s+ignore_changes = \[dashboard_json\]/,
    );
    expect(readFileSync("infra/outputs.tf", "utf8")).toContain(
      "value       = jsonencode(local.operations_dashboard)",
    );
  });
});
