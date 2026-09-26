import { PassThrough } from "node:stream";
import { fileURLToPath } from "node:url";

import { describe, expect, it } from "vitest";

import { Cdp, CdpError, chromeFlags, launchChrome } from "../../src/render/cdp.js";

const FAKE = fileURLToPath(new URL("./fake-chrome.mjs", import.meta.url));

// A client wired to two in-memory streams: what it writes, and what the "browser" says.
function wired(): { cdp: Cdp; written: string[]; say: (text: string) => void; hangUp: () => void } {
  const toBrowser = new PassThrough();
  const fromBrowser = new PassThrough();
  const written: string[] = [];
  toBrowser.on("data", (chunk: Buffer) => written.push(chunk.toString("utf8")));
  return {
    cdp: new Cdp(toBrowser, fromBrowser),
    written,
    say: (text) => fromBrowser.write(text),
    hangUp: () => fromBrowser.destroy(),
  };
}

describe("the DevTools pipe client", () => {
  it("frames each command as NUL-ended JSON with a fresh id and a session when given", async () => {
    const { cdp, written, say } = wired();
    const first = cdp.send("Page.enable");
    const second = cdp.send("DOM.enable", { a: 1 }, "S1");
    await new Promise((resolve) => setImmediate(resolve));
    expect(written.join("")).toBe(
      `${JSON.stringify({ id: 1, method: "Page.enable", params: {} })}\0` +
        `${JSON.stringify({ id: 2, method: "DOM.enable", params: { a: 1 }, sessionId: "S1" })}\0`,
    );
    // Replies out of order, and one split across two chunks.
    say(`{"id":2,"result":{"ok":2}}\0{"id":1,`);
    say(`"result":{"ok":1}}\0`);
    await expect(first).resolves.toEqual({ ok: 1 });
    await expect(second).resolves.toEqual({ ok: 2 });
  });

  it("rejects a command the browser answers with a protocol error", async () => {
    const { cdp, say } = wired();
    const pending = cdp.send("Nope.method");
    say(`{"id":1,"error":{"code":-32601,"message":"not found"}}\0`);
    await expect(pending).rejects.toBeInstanceOf(CdpError);
  });

  it("delivers events to listeners and to waitFor, by method, session and predicate", async () => {
    const { cdp, say } = wired();
    const seen: string[] = [];
    const stop = cdp.on((event) => seen.push(`${event.method}@${event.sessionId ?? "-"}`));
    const waited = cdp.waitFor("Page.loadEventFired", "S2", (params) => params.n === 2);
    say(`{"method":"Page.loadEventFired","params":{"n":1},"sessionId":"S2"}\0`);
    say(`{"method":"Page.loadEventFired","params":{"n":2},"sessionId":"S1"}\0`);
    say(`{"method":"Page.loadEventFired","params":{"n":2},"sessionId":"S2"}\0`);
    say(`{"method":"Target.targetCreated"}\0`);
    await expect(waited).resolves.toEqual({ n: 2 });
    stop();
    say(`{"method":"Page.frameNavigated","params":{}}\0`);
    await new Promise((resolve) => setImmediate(resolve));
    expect(seen).toEqual([
      "Page.loadEventFired@S2",
      "Page.loadEventFired@S1",
      "Page.loadEventFired@S2",
      "Target.targetCreated@-",
    ]);
  });

  it("ignores a reply to no pending command", async () => {
    const { cdp, say } = wired();
    say(`{"id":99,"result":{}}\0`);
    const pending = cdp.send("Page.enable");
    say(`{"id":1,"result":{"fine":true}}\0`);
    await expect(pending).resolves.toEqual({ fine: true });
  });

  it("fails every pending and later command once the pipe closes or carries garbage", async () => {
    const closed = wired();
    const pending = closed.cdp.send("Page.enable");
    closed.hangUp();
    await expect(pending).rejects.toThrow(/closed its pipe/);
    await expect(closed.cdp.send("Page.enable")).rejects.toThrow(/closed its pipe/);
    await expect(closed.cdp.waitFor("Page.loadEventFired")).rejects.toThrow(/closed its pipe/);

    const garbled = wired();
    const waiting = garbled.cdp.send("Page.enable");
    garbled.say("not json\0");
    await expect(waiting).rejects.toThrow(/not JSON/);
  });

  it("launches with R6's flags, the ratio per process, and the pipe", async () => {
    expect(chromeFlags(2.625)).toEqual(
      expect.arrayContaining([
        "--headless",
        "--font-render-hinting=none",
        "--disable-font-subpixel-positioning",
        "--disable-gpu",
        "--disable-lcd-text",
        "--force-color-profile=srgb",
        "--disable-skia-runtime-opts",
        "--hide-scrollbars",
        "--lang=en-US",
        "--force-device-scale-factor=2.625",
      ]),
    );
    const browser = launchChrome({
      executable: process.execPath,
      launcherArgs: [FAKE],
      ratio: 1,
      noSandbox: true,
    });
    await expect(browser.cdp.send("Target.createTarget", { url: "about:blank" })).resolves.toEqual({
      targetId: "T1",
    });
    await expect(browser.cdp.send("Unknown.method")).rejects.toThrow(/wasn't found/);
    await browser.close();
  });
});
