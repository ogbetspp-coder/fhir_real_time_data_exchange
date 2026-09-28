import { spawn, type ChildProcess } from "node:child_process";
import { mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";
import type { Readable, Writable } from "node:stream";

// A small Chrome DevTools Protocol client over `--remote-debugging-pipe`
// (docs/design/authority-import-renderer.md, R6): Chrome reads commands on its file descriptor 3
// and writes replies and events on 4, each message JSON ended by a NUL byte. No runtime
// dependency, no port, so nothing else on the machine can reach the browser.

export type CdpEvent = { method: string; params: Record<string, unknown>; sessionId?: string };

type Pending = {
  resolve: (value: Record<string, unknown>) => void;
  reject: (error: Error) => void;
};

export class CdpError extends Error {
  constructor(
    readonly method: string,
    readonly code: number,
    message: string,
  ) {
    super(`${method}: ${message} (${code})`);
    this.name = "CdpError";
  }
}

// A command the browser does not answer within this fails, so a hung browser fails a draw rather
// than the job's timeout.
export const COMMAND_TIMEOUT_MS = 60_000;

export class Cdp {
  private nextId = 1;
  private readonly pending = new Map<
    number,
    Pending & { method: string; timer: ReturnType<typeof setTimeout> }
  >();
  private readonly listeners = new Set<(event: CdpEvent) => void>();
  private readonly aborts = new Set<(error: Error) => void>();
  // Bytes of a message not yet ended by its NUL.
  private partial: Buffer[] = [];
  private closed: Error | undefined;

  constructor(
    private readonly output: Writable,
    input: Readable,
    private readonly timeoutMs = COMMAND_TIMEOUT_MS,
  ) {
    input.on("data", (chunk: Buffer) => this.receive(chunk));
    input.on("close", () => this.abort(new Error("the browser closed its pipe")));
    input.on("error", (error: Error) => this.abort(error));
    output.on("error", (error: Error) => this.abort(error));
  }

  // One command, answered by the reply with its id; a protocol error rejects.
  send(
    method: string,
    params: Record<string, unknown> = {},
    sessionId?: string,
  ): Promise<Record<string, unknown>> {
    if (this.closed !== undefined) return Promise.reject(this.closed);
    const id = this.nextId;
    this.nextId += 1;
    const message = JSON.stringify(
      sessionId === undefined ? { id, method, params } : { id, method, params, sessionId },
    );
    return new Promise((resolve, reject) => {
      const timer = setTimeout(() => {
        this.pending.delete(id);
        reject(new CdpError(method, -1, `no reply within ${this.timeoutMs} ms`));
      }, this.timeoutMs);
      this.pending.set(id, { resolve, reject, method, timer });
      this.output.write(`${message}\0`);
    });
  }

  // Every event, until the returned function removes the listener.
  on(listener: (event: CdpEvent) => void): () => void {
    this.listeners.add(listener);
    return () => this.listeners.delete(listener);
  }

  // Called once if the pipe fails, until the returned function removes it; at once if it has.
  onAbort(listener: (error: Error) => void): () => void {
    if (this.closed !== undefined) {
      const error = this.closed;
      queueMicrotask(() => listener(error));
      return () => undefined;
    }
    this.aborts.add(listener);
    return () => this.aborts.delete(listener);
  }

  // Each chunk is searched for NUL once, so a large reply costs its length, not its square.
  private receive(chunk: Buffer): void {
    let start = 0;
    for (let end = chunk.indexOf(0); end >= 0; end = chunk.indexOf(0, start)) {
      const text = Buffer.concat([...this.partial, chunk.subarray(start, end)]).toString("utf8");
      this.partial = [];
      start = end + 1;
      this.dispatch(text);
    }
    if (start < chunk.length) this.partial.push(chunk.subarray(start));
  }

  private dispatch(text: string): void {
    let message: {
      id?: number;
      result?: Record<string, unknown>;
      error?: { code: number; message: string };
      method?: string;
      params?: Record<string, unknown>;
      sessionId?: string;
    };
    try {
      message = JSON.parse(text) as typeof message;
    } catch {
      this.abort(new Error("the browser wrote a message that is not JSON"));
      return;
    }
    if (typeof message.id === "number") {
      const pending = this.pending.get(message.id);
      if (pending === undefined) return;
      this.pending.delete(message.id);
      clearTimeout(pending.timer);
      if (message.error !== undefined) {
        pending.reject(new CdpError(pending.method, message.error.code, message.error.message));
      } else {
        pending.resolve(message.result ?? {});
      }
      return;
    }
    if (typeof message.method === "string") {
      const event: CdpEvent = { method: message.method, params: message.params ?? {} };
      if (message.sessionId !== undefined) event.sessionId = message.sessionId;
      for (const listener of [...this.listeners]) listener(event);
    }
  }

  // Fails every pending command, and every later one, and tells the abort listeners.
  abort(error: Error): void {
    if (this.closed !== undefined) return;
    this.closed = error;
    for (const pending of this.pending.values()) {
      clearTimeout(pending.timer);
      pending.reject(error);
    }
    this.pending.clear();
    for (const listener of [...this.aborts]) listener(error);
    this.aborts.clear();
  }
}

// The flags every draw runs with (R6); the device pixel ratio is per process, since an emulated
// ratio does not change layout (R2).
export function chromeFlags(ratio: number): string[] {
  return [
    "--headless",
    "--font-render-hinting=none",
    "--disable-font-subpixel-positioning",
    "--disable-gpu",
    "--disable-lcd-text",
    "--force-color-profile=srgb",
    "--disable-skia-runtime-opts",
    "--hide-scrollbars",
    "--lang=en-US",
    `--force-device-scale-factor=${ratio}`,
    "--no-first-run",
    "--no-default-browser-check",
    "--disable-extensions",
    "--disable-background-networking",
    "--disable-component-update",
    "--disable-sync",
    "--metrics-recording-only",
    "--mute-audio",
  ];
}

export type Browser = { cdp: Cdp; close: () => Promise<void> };

export type LaunchOptions = {
  executable: string;
  ratio: number;
  // Inside the render container, which has no network and no user namespace (R1).
  noSandbox?: boolean;
  // Arguments before Chrome's own: a test runs a stand-in browser as `node <script>`.
  launcherArgs?: readonly string[];
};

export function launchChrome(options: LaunchOptions): Browser {
  const profile = mkdtempSync(path.join(tmpdir(), "renderer-profile-"));
  const args = [
    ...(options.launcherArgs ?? []),
    ...chromeFlags(options.ratio),
    ...(options.noSandbox === true ? ["--no-sandbox"] : []),
    "--remote-debugging-pipe",
    `--user-data-dir=${profile}`,
    "about:blank",
  ];
  const child: ChildProcess = spawn(options.executable, args, {
    stdio: ["ignore", "ignore", "ignore", "pipe", "pipe"],
  });
  const commands = child.stdio[3] as Writable | null;
  const replies = child.stdio[4] as Readable | null;
  if (commands === null || replies === null) {
    child.kill("SIGKILL");
    rmSync(profile, { recursive: true, force: true });
    throw new Error("the browser's debugging pipe did not open");
  }
  const cdp = new Cdp(commands, replies);
  // A browser that cannot start (a wrong path) or that dies fails every command, never throws
  // outside them.
  child.once("error", (error: Error) => cdp.abort(error));
  const exited = new Promise<void>((resolve) => {
    child.once("exit", () => resolve());
    child.once("error", () => resolve());
  });
  const settle = (milliseconds: number): Promise<void> =>
    new Promise((resolve) => setTimeout(resolve, milliseconds));
  return {
    cdp,
    close: async () => {
      try {
        await Promise.race([cdp.send("Browser.close"), settle(2000)]);
      } catch {
        // Closing is best effort; the process is killed below either way.
      }
      if (child.exitCode === null && child.signalCode === null) child.kill("SIGKILL");
      await Promise.race([exited, settle(5000)]);
      // A helper still writing to the profile can make the first removal fail (ENOTEMPTY).
      rmSync(profile, { recursive: true, force: true, maxRetries: 5, retryDelay: 100 });
    },
  };
}
