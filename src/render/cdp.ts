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

export class Cdp {
  private nextId = 1;
  private readonly pending = new Map<number, Pending & { method: string }>();
  private readonly listeners = new Set<(event: CdpEvent) => void>();
  private buffer = Buffer.alloc(0);
  private closed: Error | undefined;

  constructor(
    private readonly output: Writable,
    input: Readable,
  ) {
    input.on("data", (chunk: Buffer) => this.receive(chunk));
    input.on("close", () => this.fail(new Error("the browser closed its pipe")));
    input.on("error", (error: Error) => this.fail(error));
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
      this.pending.set(id, { resolve, reject, method });
      this.output.write(`${message}\0`);
    });
  }

  // Every event, until the returned function removes the listener.
  on(listener: (event: CdpEvent) => void): () => void {
    this.listeners.add(listener);
    return () => this.listeners.delete(listener);
  }

  // The first event of `method` (in `sessionId`, if given) that `accept` takes.
  waitFor(
    method: string,
    sessionId?: string,
    accept: (params: Record<string, unknown>) => boolean = () => true,
  ): Promise<Record<string, unknown>> {
    return new Promise((resolve, reject) => {
      if (this.closed !== undefined) {
        reject(this.closed);
        return;
      }
      const stop = this.on((event) => {
        if (event.method !== method) return;
        if (sessionId !== undefined && event.sessionId !== sessionId) return;
        if (!accept(event.params)) return;
        stop();
        resolve(event.params);
      });
    });
  }

  private receive(chunk: Buffer): void {
    this.buffer = Buffer.concat([this.buffer, chunk]);
    for (;;) {
      const end = this.buffer.indexOf(0);
      if (end < 0) return;
      const text = this.buffer.subarray(0, end).toString("utf8");
      this.buffer = this.buffer.subarray(end + 1);
      this.dispatch(text);
    }
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
      this.fail(new Error("the browser wrote a message that is not JSON"));
      return;
    }
    if (typeof message.id === "number") {
      const pending = this.pending.get(message.id);
      if (pending === undefined) return;
      this.pending.delete(message.id);
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

  private fail(error: Error): void {
    if (this.closed !== undefined) return;
    this.closed = error;
    for (const pending of this.pending.values()) pending.reject(error);
    this.pending.clear();
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
  const exited = new Promise<void>((resolve) => child.once("exit", () => resolve()));
  return {
    cdp,
    close: async () => {
      try {
        await Promise.race([
          cdp.send("Browser.close"),
          new Promise((resolve) => setTimeout(resolve, 2000)),
        ]);
      } catch {
        // Closing is best effort; the process is killed below either way.
      }
      if (child.exitCode === null) child.kill("SIGKILL");
      await exited;
      rmSync(profile, { recursive: true, force: true });
    },
  };
}
