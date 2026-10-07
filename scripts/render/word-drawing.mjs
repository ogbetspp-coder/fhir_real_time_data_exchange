import { spawnSync } from "node:child_process";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { HARDENING } from "./run.mjs";

// The one way Python runs in the Word drawing's image (docs/design/certified-word-drawing.md,
// section 4, step 3): with the renderer's hardening (scripts/render/run.mjs: no network, a
// read-only root, a tmpfs /tmp, no capabilities, no new privileges, the image's user, bounded
// processes and memory) and, read-only, only what the drawing and its checks read from the
// checkout: zone_a and label_docx, which the image does not install, the registry and mapping
// files the recompute reads, Zone A's scripts and the fixtures they draw. Standard input is passed
// through: `python -m zone_a.drawing` reads its request there. Plain JavaScript, so it runs with
// node alone.
//
// usage: node scripts/render/word-drawing.mjs <python arguments>
//   e.g. node scripts/render/word-drawing.mjs /work/zone-a/scripts/word_drawing_check.py

export const IMAGE = "word-drawing:local";

export const MOUNTS = [
  "zone-a/src",
  "label-docx-reader/src",
  "qrd/registry",
  "fhir/mappings",
  "zone-a/scripts",
  "zone-a/tests/fixtures/word-smpc",
  "qrd/sources",
  "test/fixtures/certified-word",
];

// `docker run`'s arguments for the image's Python with these arguments, the checkout at `root`.
export function dockerArgs(root, args) {
  return [
    "run",
    "--rm",
    "--interactive",
    ...HARDENING,
    ...MOUNTS.flatMap((mounted) => ["--volume", `${path.join(root, mounted)}:/work/${mounted}:ro`]),
    IMAGE,
    "/opt/python/python",
    ...args,
  ];
}

if (process.argv[1] === fileURLToPath(import.meta.url)) {
  const result = spawnSync("docker", dockerArgs(process.cwd(), process.argv.slice(2)), {
    stdio: "inherit",
  });
  if (result.error !== undefined) throw result.error;
  process.exit(result.status ?? 1);
}
