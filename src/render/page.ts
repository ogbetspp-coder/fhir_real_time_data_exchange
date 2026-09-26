import type { Cdp } from "./cdp.js";

// R2's page (docs/design/authority-import-renderer.md): one section's div on its own, at a fixed
// origin served by request interception so relative references resolve and fail the same way
// every time, with no stylesheet, a 16 px page margin as padding, and scripts disabled. The judge
// measures in an isolated world of its own.

export const ORIGIN = "https://renderer.invalid/";

export type Mode = "html" | "xml";

const XHTML = "http://www.w3.org/1999/xhtml";

// The document one mode serves. In XML mode the div must declare the XHTML namespace itself, as
// every FHIR narrative div does; the page's own elements are XHTML too.
export function pageDocument(div: string, mode: Mode): { body: string; contentType: string } {
  if (mode === "html") {
    return {
      body: `<!DOCTYPE html><meta charset="utf-8"><body style="margin:0;padding:16px">${div}</body>`,
      contentType: "text/html; charset=utf-8",
    };
  }
  return {
    body:
      `<?xml version="1.0" encoding="UTF-8"?><html xmlns="${XHTML}"><head><meta charset="utf-8"/></head>` +
      `<body style="margin:0;padding:16px">${div}</body></html>`,
    contentType: "application/xhtml+xml; charset=utf-8",
  };
}

// A response for a request other than the page itself: the picture bytes the draw carries (R2),
// keyed by absolute URL; anything else fails.
export type Resource = { body: Buffer; contentType: string };

export type Page = {
  sessionId: string;
  // The isolated world the judge evaluates in.
  contextId: number;
  // An expression evaluated in the judge's world, its value returned by value.
  evaluate: <T>(expression: string) => Promise<T>;
  // A command in this page's session.
  send: (method: string, params?: Record<string, unknown>) => Promise<Record<string, unknown>>;
  // The requests the page made that were failed, by URL (a test asserts what it expects there).
  failed: string[];
  close: () => Promise<void>;
};

export type OpenOptions = {
  div: string;
  mode: Mode;
  width: number;
  resources?: ReadonlyMap<string, Resource>;
};

// The viewport is 32 px wider than the div's content box (R2's 16 px padding on each side); its
// height is a placeholder the measurement grows to the page's own.
export async function openPage(cdp: Cdp, options: OpenOptions): Promise<Page> {
  const { targetId } = (await cdp.send("Target.createTarget", { url: "about:blank" })) as {
    targetId: string;
  };
  const { sessionId } = (await cdp.send("Target.attachToTarget", { targetId, flatten: true })) as {
    sessionId: string;
  };
  const send = (method: string, params: Record<string, unknown> = {}) =>
    cdp.send(method, params, sessionId);
  const document = pageDocument(options.div, options.mode);
  const failed: string[] = [];
  const stop = cdp.on((event) => {
    if (event.sessionId !== sessionId || event.method !== "Fetch.requestPaused") return;
    const { requestId, request } = event.params as { requestId: string; request: { url: string } };
    const resource = options.resources?.get(request.url);
    if (request.url === ORIGIN) {
      void send("Fetch.fulfillRequest", {
        requestId,
        responseCode: 200,
        responseHeaders: [{ name: "Content-Type", value: document.contentType }],
        body: Buffer.from(document.body, "utf8").toString("base64"),
      });
    } else if (resource !== undefined) {
      void send("Fetch.fulfillRequest", {
        requestId,
        responseCode: 200,
        responseHeaders: [{ name: "Content-Type", value: resource.contentType }],
        body: resource.body.toString("base64"),
      });
    } else {
      failed.push(request.url);
      void send("Fetch.failRequest", { requestId, errorReason: "BlockedByClient" });
    }
  });
  await send("Fetch.enable", { patterns: [{ urlPattern: "*" }] });
  await send("Page.enable");
  await send("Emulation.setScriptExecutionDisabled", { value: true });
  await send("Emulation.setDeviceMetricsOverride", {
    width: options.width + 32,
    height: 600,
    deviceScaleFactor: 0,
    mobile: false,
  });
  const loaded = cdp.waitFor("Page.loadEventFired", sessionId);
  const navigation = (await send("Page.navigate", { url: ORIGIN })) as {
    frameId: string;
    errorText?: string;
  };
  if (navigation.errorText !== undefined) {
    stop();
    throw new Error(`the page did not load: ${navigation.errorText}`);
  }
  await loaded;
  const { executionContextId } = (await send("Page.createIsolatedWorld", {
    frameId: navigation.frameId,
    worldName: "renderer-judge",
    grantUniveralAccess: false,
  })) as { executionContextId: number };
  const evaluate = async <T>(expression: string): Promise<T> => {
    const reply = (await send("Runtime.evaluate", {
      expression,
      contextId: executionContextId,
      returnByValue: true,
      awaitPromise: true,
    })) as { result: { value?: unknown }; exceptionDetails?: { text: string } };
    if (reply.exceptionDetails !== undefined) {
      throw new Error(`evaluation failed in the page: ${reply.exceptionDetails.text}`);
    }
    return reply.result.value as T;
  };
  return {
    sessionId,
    contextId: executionContextId,
    evaluate,
    send,
    failed,
    close: async () => {
      stop();
      await cdp.send("Target.closeTarget", { targetId });
    },
  };
}
