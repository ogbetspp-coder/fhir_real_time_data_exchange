import { spawnSync } from "node:child_process";
import path from "node:path";
import { fileURLToPath } from "node:url";

// The one way a script runs in the renderer image (docs/design/authority-import-renderer.md, R1
// and R6): every `renderer:*` npm script but the image's build is `node scripts/render/run.mjs
// <script>` (test/render/run.test.ts holds them to it). Chrome runs there without its sandbox
// (RENDERER_NO_SANDBOX=1), which rests on this isolation: no network, a read-only root, no
// capabilities and no new privileges, bounded processes and memory, and only the paths the
// scripts read mounted, read-only, never the whole working tree. The smoke check asserts the
// isolation from inside before it draws anything. Plain JavaScript, so it runs with node alone.
//
// usage: node scripts/render/run.mjs scripts/render/<script>.ts [arguments]

export const IMAGE = "renderer:local";

// The hardening itself, which the Word drawing's image shares (scripts/render/word-drawing.mjs).
export const HARDENING = [
  "--network",
  "none",
  "--read-only",
  "--tmpfs",
  "/tmp",
  "--shm-size=1g",
  "--cap-drop=ALL",
  "--security-opt=no-new-privileges",
  "--pids-limit=2048",
  "--memory=6g",
];

export const ISOLATION = [...HARDENING, "--env", "RENDERER_NO_SANDBOX=1"];

// What the scripts under scripts/render read: their code and its dependencies, the fixtures and
// the pinned labels.
export const MOUNTS = [
  "package.json",
  "tsconfig.json",
  "node_modules",
  "src",
  "scripts/render",
  "test/fixtures",
  "labels/ema-epi",
];

const SCRIPT = /^scripts\/render\/[a-z-]+\.ts$/u;

// `docker run`'s arguments for one script, with the working tree at `root`.
export function dockerArgs(root, script, args = []) {
  if (!SCRIPT.test(script)) throw new Error(`not a script of scripts/render: ${script}`);
  return [
    "run",
    "--rm",
    ...ISOLATION,
    ...MOUNTS.flatMap((mounted) => ["--volume", `${path.join(root, mounted)}:/work/${mounted}:ro`]),
    IMAGE,
    "node",
    "--import",
    "tsx",
    script,
    ...args,
  ];
}

if (process.argv[1] === fileURLToPath(import.meta.url)) {
  const [script = "", ...args] = process.argv.slice(2);
  const result = spawnSync("docker", dockerArgs(process.cwd(), script, args), {
    stdio: "inherit",
  });
  if (result.error !== undefined) throw result.error;
  process.exit(result.status ?? 1);
}
