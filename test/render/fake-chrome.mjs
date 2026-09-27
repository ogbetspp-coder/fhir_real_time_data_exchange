// A stand-in for chrome-headless-shell in the renderer's unit tests: it speaks the DevTools
// protocol over the pipe Chrome uses (commands on fd 3, replies and events on fd 4, NUL-ended
// JSON) and answers the commands src/render/page.ts sends, with the page's requests played as
// Fetch.requestPaused events. Behaviour is chosen by FAKE_CHROME: "ok", "navigate-error",
// "evaluate-throws", "garbage", "crash" (the target crashes instead of loading), "renavigate" (the
// page asks for itself again and navigates after it loaded), "refuse-replies" (replies to paused
// requests and the target's close fail), "no-load" (the page never loads), "unresolved" (a
// page-side node cannot be resolved by reference), "whitespace-node" and "lost-node" (DevTools
// keeps no node for the text), "fonts-error" (another protocol error).
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
          params: {
            requestId: `R${index}`,
            request: { url },
            resourceType: index === 0 ? "Document" : "Image",
          },
        });
      }
      if (mode === "no-load") return undefined;
      if (mode === "crash") {
        setTimeout(
          () => write({ method: "Inspector.targetCrashed", sessionId: "S1", params: {} }),
          20,
        );
        return undefined;
      }
      setTimeout(() => write({ method: "Page.loadEventFired", sessionId: "S1", params: {} }), 20);
      if (mode === "renavigate") {
        setTimeout(() => {
          write({
            method: "Fetch.requestPaused",
            sessionId: "S1",
            params: {
              requestId: "R9",
              request: { url: "https://renderer.invalid/" },
              resourceType: "Document",
            },
          });
          write({ method: "Page.frameNavigated", sessionId: "S1", params: {} });
        }, 40);
      }
      return undefined;
    case "Fetch.fulfillRequest":
    case "Fetch.failRequest":
      fulfilled.push({ method, ...params });
      if (mode === "refuse-replies")
        return write({ id, error: { code: -32000, message: "Invalid InterceptionId" } });
      return answer({});
    case "Target.closeTarget":
      if (mode === "refuse-replies")
        return write({ id, error: { code: -32000, message: "No target" } });
      return answer({});
    case "Page.createIsolatedWorld":
      return answer({ executionContextId: 7 });
    case "Runtime.evaluate":
      if (mode === "evaluate-throws")
        return answer({ result: {}, exceptionDetails: { text: "Uncaught" } });
      if (params.expression === "handled") return answer({ result: { value: fulfilled } });
      // A page-side node, by reference (DOM.requestNode resolves it).
      if (params.returnByValue === false) {
        return answer({
          result:
            mode === "unresolved"
              ? { type: "undefined" }
              : { objectId: `node-${String(params.expression)}` },
        });
      }
      // The judge's page-side reads (src/render/measure.ts), answered for the tree DOM.getDocument
      // gives: one text node, under the ol.
      if (String(params.expression).includes("fontFamily")) {
        const run = {
          element: 1,
          family: '"Times New Roman"',
          weight: 400,
          style: "normal",
          size: "16px",
          text: "ab",
        };
        return answer({ result: { value: [run] } });
      }
      if (String(params.expression).includes("parsererror")) {
        return answer({
          result: {
            value: {
              parserError: false,
              divPadding: "0px 0px 0px 0px",
              divBorder: "0px 0px 0px 0px",
              divWidth: 813,
            },
          },
        });
      }
      if (String(params.expression).includes("getClientRects")) {
        return answer({ result: { value: [{ element: 1, heights: [18, 18] }] } });
      }
      return answer({
        result: { value: { expression: params.expression, contextId: params.contextId } },
      });
    // A div holding an ol of two items, the second with no marker node, and a stray marker.
    case "DOM.getDocument":
      return answer({
        root: {
          nodeType: 9,
          localName: "",
          backendNodeId: 1,
          children: [
            {
              nodeType: 1,
              localName: "html",
              backendNodeId: 2,
              children: [
                {
                  nodeType: 1,
                  localName: "body",
                  backendNodeId: 3,
                  children: [
                    { nodeType: 3, localName: "", backendNodeId: 4 },
                    {
                      nodeType: 1,
                      localName: "div",
                      backendNodeId: 10,
                      children: [
                        {
                          nodeType: 1,
                          localName: "ol",
                          backendNodeId: 11,
                          children: [
                            { nodeType: 1, localName: "li", backendNodeId: 12 },
                            { nodeType: 3, nodeId: 13, localName: "", backendNodeId: 13 },
                            { nodeType: 1, localName: "li", backendNodeId: 14 },
                          ],
                        },
                      ],
                    },
                  ],
                },
              ],
            },
          ],
        },
      });
    case "DOM.requestNode":
      return answer({ nodeId: mode === "whitespace-node" ? 0 : 13 });
    case "CSS.getPlatformFontsForNode":
      if (mode === "lost-node")
        return write({ id, error: { code: -32000, message: "Could not find node with given id" } });
      if (mode === "fonts-error")
        return write({ id, error: { code: -32601, message: "CSS agent is not enabled" } });
      return answer({
        fonts: [
          {
            postScriptName: `LiberationSerif`,
            familyName: `Liberation Serif`,
            glyphCount: params.nodeId,
          },
        ],
      });
    case "Accessibility.getFullAXTree":
      return answer({
        nodes: [
          { nodeId: "a", role: { value: "listitem" }, backendDOMNodeId: 12 },
          { nodeId: "b", parentId: "a", role: { value: "ListMarker" }, name: { value: "1. " } },
          { nodeId: "c", role: { value: "listitem" }, backendDOMNodeId: 14 },
          { nodeId: "d", role: { value: "ListMarker" }, name: { value: "orphan" } },
          { nodeId: "e", parentId: "zz", role: { value: "ListMarker" } },
        ],
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
