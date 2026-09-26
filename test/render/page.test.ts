import { fileURLToPath } from "node:url";

import { afterEach, describe, expect, it } from "vitest";

import { launchChrome, type Browser } from "../../src/render/cdp.js";
import { readElements, readMarkers, readTexts } from "../../src/render/measure.js";
import { ORIGIN, openPage, pageDocument } from "../../src/render/page.js";

const FAKE = fileURLToPath(new URL("./fake-chrome.mjs", import.meta.url));
const DIV = '<div xmlns="http://www.w3.org/1999/xhtml"><p>x</p></div>';

let browser: Browser | undefined;
function fake(mode: string): Browser {
  process.env.FAKE_CHROME = mode;
  browser = launchChrome({ executable: process.execPath, launcherArgs: [FAKE], ratio: 1 });
  return browser;
}
afterEach(async () => {
  await browser?.close();
  browser = undefined;
  delete process.env.FAKE_CHROME;
});

describe("R2's page", () => {
  it("serves the div alone in each mode, with the 16 px page margin as padding", () => {
    expect(pageDocument(DIV, "html")).toEqual({
      body: `<!DOCTYPE html><meta charset="utf-8"><body style="margin:0;padding:16px">${DIV}</body>`,
      contentType: "text/html; charset=utf-8",
    });
    const xml = pageDocument(DIV, "xml");
    expect(xml.contentType).toBe("application/xhtml+xml; charset=utf-8");
    expect(xml.body).toBe(
      '<?xml version="1.0" encoding="UTF-8"?><html xmlns="http://www.w3.org/1999/xhtml"><head><meta charset="utf-8"/></head>' +
        `<body style="margin:0;padding:16px">${DIV}</body></html>`,
    );
  });

  it("fulfils the page at the fixed origin and the carried pictures, and fails everything else", async () => {
    const { cdp } = fake("ok");
    const picture = { body: Buffer.from("png-bytes"), contentType: "image/png" };
    const page = await openPage(cdp, {
      div: DIV,
      mode: "html",
      width: 813,
      resources: new Map([[`${ORIGIN}p.png`, picture]]),
    });
    expect(page.sessionId).toBe("S1");
    expect(page.contextId).toBe(7);
    expect(page.failed).toEqual(["https://example.org/x.png"]);
    const handled =
      await page.evaluate<
        { method: string; requestId: string; body?: string; errorReason?: string }[]
      >("handled");
    expect(handled.map(({ method, requestId }) => `${method}:${requestId}`)).toEqual([
      "Fetch.fulfillRequest:R0",
      "Fetch.fulfillRequest:R1",
      "Fetch.failRequest:R2",
    ]);
    expect(Buffer.from(handled[0]?.body ?? "", "base64").toString("utf8")).toBe(
      pageDocument(DIV, "html").body,
    );
    expect(Buffer.from(handled[1]?.body ?? "", "base64").toString("utf8")).toBe("png-bytes");
    expect(handled[2]?.errorReason).toBe("BlockedByClient");
    await expect(page.evaluate("1 + 1")).resolves.toEqual({ expression: "1 + 1", contextId: 7 });
    await page.close();
  });

  it("refuses a page that did not load, and an evaluation that throws", async () => {
    await expect(
      openPage(fake("navigate-error").cdp, { div: DIV, mode: "xml", width: 360 }),
    ).rejects.toThrow(/did not load: net::ERR_FAILED/);
    await browser?.close();
    const page = await openPage(fake("evaluate-throws").cdp, {
      div: DIV,
      mode: "html",
      width: 360,
    });
    await expect(page.evaluate("boom")).rejects.toThrow(/evaluation failed in the page: Uncaught/);
  });

  it("reads each list item's marker by its pre-order index within the div", async () => {
    const page = await openPage(fake("ok").cdp, { div: DIV, mode: "html", width: 813 });
    // div 0, ol 1, li 2, li 3: only the first item has a marker node under it.
    expect([...(await readMarkers(page))]).toEqual([[2, "1. "]]);
    // The page-side reads are expressions evaluated in the judge's world.
    const read = await readElements(page);
    expect(read).toEqual(expect.objectContaining({ contextId: 7 }));
    expect(await readTexts(page)).toEqual(expect.objectContaining({ contextId: 7 }));
  });

  it("survives replies to paused requests, and the close, failing", async () => {
    const page = await openPage(fake("refuse-replies").cdp, { div: DIV, mode: "html", width: 813 });
    expect(page.failed).toEqual(["https://renderer.invalid/p.png", "https://example.org/x.png"]);
    await expect(page.close()).resolves.toBeUndefined();
  });

  it("refuses a page that does not load in time", async () => {
    await expect(
      openPage(fake("no-load").cdp, { div: DIV, mode: "html", width: 813, loadTimeoutMs: 50 }),
    ).rejects.toThrow(/Page.loadEventFired did not arrive within 50 ms/);
  });

  it("refuses a page whose target crashes before it loads", async () => {
    await expect(
      openPage(fake("crash").cdp, { div: DIV, mode: "html", width: 813 }),
    ).rejects.toThrow(/the page crashed/);
  });

  it("serves the page once, and refuses to read a page that navigated after it loaded", async () => {
    const page = await openPage(fake("renavigate").cdp, { div: DIV, mode: "html", width: 813 });
    await new Promise((resolve) => setTimeout(resolve, 100));
    expect(page.failed).toContain("https://renderer.invalid/");
    await expect(page.evaluate("1")).rejects.toThrow(/navigated after it loaded/);
  });

  it("fails every command once the browser writes something that is not the protocol", async () => {
    await expect(fake("garbage").cdp.send("Target.createTarget")).rejects.toThrow(/not JSON/);
  });
});
