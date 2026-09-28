import { spawnSync } from "node:child_process";
import { chmodSync, mkdtempSync, readFileSync, readdirSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";

import { afterAll, describe, expect, it } from "vitest";

// No credential in a process's arguments (audit B08, D-7). A process's argument list can be read
// by every other process on the host while it runs (ps, /proc/<pid>/cmdline), and until then the
// deploy scripts passed access tokens to curl as `--header "Authorization: Bearer …"` or
// `--user oauth2accesstoken:…`. They now hand curl a header file through a pipe
// (common.sh, ema_flow_header), which only curl reads.

const scripts = readdirSync("scripts/gcp")
  .filter((name) => name.endsWith(".sh"))
  .map((name) => `scripts/gcp/${name}`);

describe("the deploy scripts' credentials", () => {
  it("are never a curl argument", () => {
    for (const file of scripts) {
      const text = readFileSync(file, "utf8");
      const argument =
        /(?:--header|-H) "(?:X-Serverless-)?Authorization: (?:Bearer|Basic)|--user "?oauth2accesstoken/.exec(
          text,
        );
      expect([file, argument?.[0]]).toEqual([file, undefined]);
    }
  });

  it("reach curl through a header file, and the value never appears in its arguments", () => {
    const dir = mkdtempSync(path.join(tmpdir(), "credential-arguments-"));
    afterAll(() => rmSync(dir, { recursive: true, force: true }));
    const log = path.join(dir, "argv");
    writeFileSync(
      path.join(dir, "curl"),
      `#!/usr/bin/env bash
printf '%s\\n' "$@" >"${log}"
while [ $# -gt 0 ]; do [ "$1" = --header ] && cat "\${2#@}" >"${log}.header"; shift; done
`,
    );
    chmodSync(path.join(dir, "curl"), 0o755);
    const run = spawnSync(
      "bash",
      [
        "-c",
        'source scripts/gcp/common.sh; token=ya29.secret; curl --silent --header @<(ema_flow_header Authorization "Bearer ${token}") https://x',
      ],
      { encoding: "utf8", env: { PATH: `${dir}:${process.env.PATH ?? ""}` } },
    );
    expect(run.status).toBe(0);
    expect(readFileSync(log, "utf8")).not.toContain("ya29.secret");
    expect(readFileSync(`${log}.header`, "utf8")).toBe("Authorization: Bearer ya29.secret\n");
  });

  it("share one response summariser, in common.sh", () => {
    const defining = scripts.filter((file) =>
      /^summarize_response\(\) \{/m.test(readFileSync(file, "utf8")),
    );
    expect(defining).toEqual(["scripts/gcp/common.sh"]);
  });
});
