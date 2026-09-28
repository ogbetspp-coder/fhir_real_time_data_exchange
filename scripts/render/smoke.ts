import { readFileSync, writeFileSync } from "node:fs";
import { connect } from "node:net";

import { launchChrome } from "../../src/render/cdp.js";
import { BINDINGS, boundFace, NOT_DRAWN_RANGES } from "../../src/render/fonts.js";
import { openPage } from "../../src/render/page.js";
import { EXECUTABLE, NO_SANDBOX } from "./sections.js";

// The renderer image's smoke check (docs/design/authority-import-renderer.md, R6), run inside the
// image with no network: where Chrome's sandbox is off, the container's isolation that justifies
// it holds; the browser starts, both modes load, a request for anything but the page fails, and
// every family R6 binds (src/render/fonts.ts, BINDINGS, the fontconfig's aliases) is drawn in
// the pinned face it names in each of its four faces, read back through
// CSS.getPlatformFontsForNode.
//
// usage (inside the image): node --import tsx scripts/render/smoke.ts

const FACES: readonly [string, string, number, string][] = [
  ["normal", "normal", 400, "normal"],
  ["bold", "normal", 700, "normal"],
  ["normal", "italic", 400, "italic"],
  ["bold", "italic", 700, "italic"],
];

// The isolation `--no-sandbox` rests on (R1, R6; scripts/render/run.mjs): no network but the
// loopback, a read-only root and workspace, no capabilities, and no new privileges.
async function isolation(): Promise<string[]> {
  const found: string[] = [];
  const reaches = (host: string) =>
    new Promise<boolean>((resolve) => {
      const socket = connect({ host, port: 443, timeout: 3000 });
      socket.once("connect", () => {
        socket.destroy();
        resolve(true);
      });
      socket.once("timeout", () => {
        socket.destroy();
        resolve(false);
      });
      socket.once("error", () => resolve(false));
    });
  // Documentation addresses (RFC 5737, RFC 3849): nothing answers there, but a route would let
  // the attempt leave the container.
  const reached = (await reaches("192.0.2.1")) || (await reaches("2001:db8::1"));
  const routes = readFileSync("/proc/net/route", "utf8").trim().split("\n").slice(1);
  // IPv6 too (audit B07, carried from B11's review): every route but the loopback's, whose device
  // is the last field. No file means the kernel has no IPv6, so no IPv6 route.
  let routes6: string[] = [];
  try {
    routes6 = readFileSync("/proc/net/ipv6_route", "utf8")
      .trim()
      .split("\n")
      .filter((line) => line.trim() !== "" && line.trim().split(/\s+/u).at(-1) !== "lo");
  } catch (error) {
    if ((error as NodeJS.ErrnoException).code !== "ENOENT")
      found.push("/proc/net/ipv6_route unreadable");
  }
  if (reached || routes.length > 0 || routes6.length > 0) {
    found.push(
      `the container has a network (${routes.length} IPv4 routes, ${routes6.length} IPv6 routes)`,
    );
  }
  for (const where of ["/work/.renderer-probe", "/home/node/.renderer-probe"]) {
    try {
      writeFileSync(where, "");
      found.push(`${where} is writable`);
    } catch (error) {
      const code = (error as NodeJS.ErrnoException).code;
      if (code !== "EROFS") found.push(`${where}: ${code ?? "an error"}, not EROFS`);
    }
  }
  const status = readFileSync("/proc/self/status", "utf8");
  const field = (name: string): string | undefined =>
    new RegExp(`^${name}:\\s*(\\S+)$`, "mu").exec(status)?.[1];
  if (field("CapBnd") !== "0000000000000000") found.push(`capabilities ${field("CapBnd")}`);
  if (field("NoNewPrivs") !== "1") found.push("new privileges allowed");
  if (process.getuid?.() === 0) found.push("running as root");
  return found;
}

const failures: string[] = NO_SANDBOX ? await isolation() : [];
if (failures.length > 0) {
  console.error(`Chrome's sandbox is off, but: ${failures.join("; ")}`);
  process.exit(1);
}
const spans: { id: string; expected: string }[] = [];
const parts: string[] = [];
BINDINGS.forEach(([family], f) => {
  FACES.forEach(([weight, style, numeric, computed], s) => {
    const id = `f${f}s${s}`;
    spans.push({ id, expected: boundFace(family, numeric, computed) ?? "(unbound)" });
    const quoted = family.includes(" ") ? `'${family}'` : family;
    parts.push(
      `<span id="${id}" style="font-family:${quoted};font-weight:${weight};font-style:${style}">Hamburgefonstiv 0123</span>`,
    );
  });
});
const div = `<div xmlns="http://www.w3.org/1999/xhtml"><p>${parts.join(" ")}<img src="picture.png" alt=""/></p></div>`;

const browser = launchChrome({ executable: EXECUTABLE, ratio: 1, noSandbox: NO_SANDBOX });
try {
  for (const mode of ["html", "xml"] as const) {
    const page = await openPage(browser.cdp, { div, mode, width: 813 });
    const parserError = await page.evaluate<boolean>(
      "document.getElementsByTagName('parsererror').length > 0",
    );
    if (parserError) failures.push(`${mode}: the page did not parse`);
    if (!page.failed.some((url) => url.endsWith("/picture.png"))) {
      failures.push(`${mode}: the picture request was not failed (${page.failed.join(", ")})`);
    }
    await page.send("DOM.enable");
    await page.send("CSS.enable");
    const { root } = (await page.send("DOM.getDocument", { depth: -1 })) as {
      root: { nodeId: number };
    };
    for (const { id, expected } of spans) {
      const { nodeId } = (await page.send("DOM.querySelector", {
        nodeId: root.nodeId,
        selector: `#${id}`,
      })) as { nodeId: number };
      const { fonts } = (await page.send("CSS.getPlatformFontsForNode", { nodeId })) as {
        fonts: { familyName: string; postScriptName: string }[];
      };
      const names = fonts.map((font) => font.postScriptName);
      if (names.length !== 1 || names[0] !== expected) {
        failures.push(`${mode}: #${id} drawn in ${names.join(", ") || "nothing"}, not ${expected}`);
      }
    }
    await page.close();
  }

  // R6's substitutions, by pixels, in every pinned family: a no-break space drawn as the space's
  // glyph, a non-breaking hyphen as the hyphen's, and a soft hyphen drawn only at a line break,
  // as a hyphen. Each pair is drawn alone at the same place and captured; equal PNG bytes are
  // equal pixels.
  const families = ["'Times New Roman'", "Arial", "Calibri", "Cambria"];
  const pairs: [string, string, string, readonly string[]][] = [
    ["no-break space", "a\u00a0b", "a b", families],
    // Only in Liberation's faces (src/render/fonts.ts, SUBSTITUTIONS).
    ["non-breaking hyphen", "1\u20112", "1-2", ["'Times New Roman'", "Arial"]],
    ["soft hyphen within a line", "ab\u00adcd", "abcd", families],
  ];
  for (const family of families) {
    for (const [what, substituted, plain, where] of pairs) {
      if (!where.includes(family)) continue;
      const shots: string[] = [];
      for (const text of [substituted, plain]) {
        const page = await openPage(browser.cdp, {
          div: `<div xmlns="http://www.w3.org/1999/xhtml"><p style="margin:0;font-family:${family};font-size:24px">${text}</p></div>`,
          mode: "html",
          width: 200,
        });
        const { data } = (await page.send("Page.captureScreenshot", {
          format: "png",
          clip: { x: 16, y: 16, width: 200, height: 40, scale: 1 },
        })) as { data: string };
        shots.push(data);
        await page.close();
      }
      if (shots[0] !== shots[1])
        failures.push(`${family}: the ${what} is not drawn as its substitute`);
    }
    // Each range of R3's closed list of code points drawn as nothing (src/render/fonts.ts,
    // NOT_DRAWN_RANGES), its first and last, between letters: the same pixels as the letters alone.
    const plainPage = await openPage(browser.cdp, {
      div: `<div xmlns="http://www.w3.org/1999/xhtml"><p style="margin:0;font-family:${family};font-size:24px">abc</p></div>`,
      mode: "html",
      width: 200,
    });
    const plainShot = (
      (await plainPage.send("Page.captureScreenshot", {
        format: "png",
        clip: { x: 16, y: 16, width: 200, height: 40, scale: 1 },
      })) as { data: string }
    ).data;
    await plainPage.close();
    for (const [low, high] of NOT_DRAWN_RANGES) {
      for (const codePoint of new Set([low, high])) {
        // ASCII whitespace controls and the soft hyphen are drawn as a space or at a break.
        if (codePoint < 0x20 || codePoint === 0xad) continue;
        const page = await openPage(browser.cdp, {
          div: `<div xmlns="http://www.w3.org/1999/xhtml"><p style="margin:0;font-family:${family};font-size:24px">a${String.fromCodePoint(codePoint)}bc</p></div>`,
          mode: "html",
          width: 200,
        });
        const { data } = (await page.send("Page.captureScreenshot", {
          format: "png",
          clip: { x: 16, y: 16, width: 200, height: 40, scale: 1 },
        })) as { data: string };
        await page.close();
        if (data !== plainShot) {
          failures.push(
            `${family}: U+${codePoint.toString(16).toUpperCase()} is drawn as something`,
          );
        }
      }
    }

    // At a line break the soft hyphen is drawn, as a hyphen: its box has the hyphen's width.
    const page = await openPage(browser.cdp, {
      div: `<div xmlns="http://www.w3.org/1999/xhtml"><p style="margin:0;font-family:${family};font-size:24px">aaaaaaaaaa\u00adbbbbbbbbbb</p><p style="margin:0;font-family:${family};font-size:24px"><span id="h">-</span></p></div>`,
      mode: "html",
      width: 150,
    });
    const [soft, hyphen] = await page.evaluate<[number, number]>(
      `(() => { const t = document.querySelector("p").firstChild; const r = new Range(); const at = t.data.indexOf("\u00ad"); r.setStart(t, at); r.setEnd(t, at + 1); const b = r.getBoundingClientRect(); return [b.width, document.getElementById("h").getBoundingClientRect().width]; })()`,
    );
    if (Math.abs(soft - hyphen) > 0.01 || hyphen <= 0) {
      failures.push(
        `${family}: a soft hyphen at a line break is ${soft} px wide, a hyphen ${hyphen} px`,
      );
    }
    await page.close();
  }
} finally {
  await browser.close();
}

if (failures.length > 0) {
  console.error(failures.join("\n"));
  process.exit(1);
}
console.log(
  `renderer image: ${NO_SANDBOX ? "isolated as --no-sandbox requires; " : ""}both modes load offline, requests but the page's fail, ${spans.length} family and face bindings drawn in their pinned faces, R6's substitutions drawn as their substitutes`,
);
