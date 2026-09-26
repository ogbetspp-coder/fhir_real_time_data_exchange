// A stand-in for chrome-headless-shell in the renderer's unit tests: it speaks the DevTools
// protocol over the pipe Chrome uses (commands on fd 3, replies and events on fd 4, NUL-ended
// JSON) and answers the commands src/render/page.ts sends, with the page's requests played as
// Fetch.requestPaused events. Behaviour is chosen by FAKE_CHROME: "ok", "navigate-error",
// "evaluate-throws", "garbage".
import { createReadStream, createWriteStream } from "node:fs";
import { setTimeout } from "node:timers";

const mode = process.env.FAKE_CHROME ?? "ok";
const input = createReadStream("", { fd: 3 });
const output = createWriteStream("", { fd: 4 });
const write = (message) => output.write(`${JSON.stringify(message)}\0`);
const fulfilled = [];
let buffer = "";

function reply(message) {
  const { id, method, params = {}, sessionId } = message;
  const answer = (result) => write({ id, result, ...(sessionId ? { sessionId } : {}) });
  switch (method) {
    case "Target.createTarget":
      return answer({ targetId: "T1" });
    case "Target.attachToTarget":
      return answer({ sessionId: "S1" });
    case "Page.navigate":
      if (mode === "navigate-error") return answer({ frameId: "F1", errorText: "net::ERR_FAILED" });
      answer({ frameId: "F1" });
      for (const [index, url] of [
        "https://renderer.invalid/",
        "https://renderer.invalid/p.png",
        "https://example.org/x.png",
      ].entries()) {
        write({
          method: "Fetch.requestPaused",
          sessionId: "S1",
          params: { requestId: `R${index}`, request: { url } },
        });
      }
      setTimeout(() => write({ method: "Page.loadEventFired", sessionId: "S1", params: {} }), 20);
      return undefined;
    case "Fetch.fulfillRequest":
    case "Fetch.failRequest":
      fulfilled.push({ method, ...params });
      return answer({});
    case "Page.createIsolatedWorld":
      return answer({ executionContextId: 7 });
    case "Runtime.evaluate":
      if (mode === "evaluate-throws")
        return answer({ result: {}, exceptionDetails: { text: "Uncaught" } });
      if (params.expression === "handled") return answer({ result: { value: fulfilled } });
      return answer({
        result: { value: { expression: params.expression, contextId: params.contextId } },
      });
    case "Unknown.method":
      return write({ id, error: { code: -32601, message: "'Unknown.method' wasn't found" } });
    case "Browser.close":
      answer({});
      return setTimeout(() => process.exit(0), 10);
    default:
      return answer({});
  }
}

input.on("data", (chunk) => {
  buffer += chunk.toString("utf8");
  for (let end = buffer.indexOf("\0"); end >= 0; end = buffer.indexOf("\0")) {
    const text = buffer.slice(0, end);
    buffer = buffer.slice(end + 1);
    if (mode === "garbage") {
      output.write("not json\0");
      continue;
    }
    reply(JSON.parse(text));
  }
});
