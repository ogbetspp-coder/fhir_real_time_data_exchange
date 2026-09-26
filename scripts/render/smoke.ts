import { launchChrome } from "../../src/render/cdp.js";
import { openPage } from "../../src/render/page.js";

// The renderer image's smoke check (docs/design/authority-import-renderer.md, R6), run inside the
// image with no network: the browser starts, both modes load, a request for anything but the page
// fails, and every family the fontconfig binds is drawn in the pinned face it names, read back
// through CSS.getPlatformFontsForNode.
//
// usage (inside the image): node --import tsx scripts/render/smoke.ts

const EXECUTABLE =
  process.env.RENDERER_CHROME ??
  "/opt/renderer/chrome-headless-shell-linux64/chrome-headless-shell";

const BINDINGS: readonly [string, string][] = [
  ["Times New Roman", "LiberationSerif"],
  ["Times", "LiberationSerif"],
  ["serif", "LiberationSerif"],
  ["Arial", "LiberationSans"],
  ["Helvetica", "LiberationSans"],
  ["sans-serif", "LiberationSans"],
  ["Calibri", "Carlito"],
  ["Cambria", "Caladea"],
];
const FACES: readonly [string, string, string][] = [
  ["normal", "normal", "Regular"],
  ["bold", "normal", "Bold"],
  ["normal", "italic", "Italic"],
  ["bold", "italic", "BoldItalic"],
];

const failures: string[] = [];
const spans: { id: string; expected: string }[] = [];
const parts: string[] = [];
BINDINGS.forEach(([family, face], f) => {
  FACES.forEach(([weight, style, suffix], s) => {
    const id = `f${f}s${s}`;
    // The Liberation faces' regular PostScript names carry no suffix (measured in the image).
    const regularBare = face.startsWith("Liberation") && suffix === "Regular";
    spans.push({ id, expected: regularBare ? face : `${face}-${suffix}` });
    const quoted = family.includes(" ") ? `'${family}'` : family;
    parts.push(
      `<span id="${id}" style="font-family:${quoted};font-weight:${weight};font-style:${style}">Hamburgefonstiv 0123</span>`,
    );
  });
});
const div = `<div xmlns="http://www.w3.org/1999/xhtml"><p>${parts.join(" ")}<img src="picture.png" alt=""/></p></div>`;

const browser = launchChrome({
  executable: EXECUTABLE,
  ratio: 1,
  noSandbox: process.env.RENDERER_NO_SANDBOX === "1",
});
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
} finally {
  await browser.close();
}

if (failures.length > 0) {
  console.error(failures.join("\n"));
  process.exit(1);
}
console.log(
  `renderer image: both modes load offline, requests but the page's fail, ${spans.length} family and face bindings drawn in their pinned faces`,
);
