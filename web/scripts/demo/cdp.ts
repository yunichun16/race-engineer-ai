/**
 * A small Chrome DevTools Protocol client for the demo recorder (plan 12): it starts Google Chrome
 * headless with a throwaway profile and drives one page over Node's built-in WebSocket. No
 * packages, no Puppeteer.
 *
 * Chrome is started with `--remote-debugging-port=0` and writes the port it picked to
 * `DevToolsActivePort` in the profile, so two recorders never fight over a port. The flags keep
 * it off the network apart from the pages it is sent to (no sync, component updates, metrics or
 * translation), and off the macOS keychain (`--use-mock-keychain`).
 */

import { spawn, type ChildProcess } from "node:child_process";
import { existsSync, readFileSync } from "node:fs";
import { setTimeout as sleep } from "node:timers/promises";

export const DEFAULT_CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome";

export interface ChromeOptions {
  /** The browser binary; `CHROME_PATH`, then Google Chrome in /Applications. */
  binary?: string;
  /** A throwaway profile folder: Chrome 136+ refuses remote debugging on the default profile. */
  profile: string;
  width: number;
  height: number;
  timeoutMs?: number;
}

export interface Chrome {
  port: number;
  process: ChildProcess;
  close: () => Promise<void>;
}

/** Starts Chrome headless and waits for its DevTools port. */
export async function launchChrome(options: ChromeOptions): Promise<Chrome> {
  const binary = options.binary ?? process.env.CHROME_PATH ?? DEFAULT_CHROME;
  if (!existsSync(binary)) throw new Error(`no Chrome at ${binary} (set CHROME_PATH or --chrome)`);
  const args = [
    "--headless",
    `--user-data-dir=${options.profile}`,
    "--remote-debugging-port=0",
    `--window-size=${options.width},${options.height}`,
    "--force-device-scale-factor=1",
    "--hide-scrollbars",
    "--mute-audio",
    "--no-first-run",
    "--no-default-browser-check",
    "--use-mock-keychain",
    "--password-store=basic",
    "--disable-background-networking",
    "--disable-component-update",
    "--disable-default-apps",
    "--disable-sync",
    "--metrics-recording-only",
    "--no-pings",
    "--disable-features=Translate,OptimizationHints,MediaRouter",
    "about:blank",
  ];
  const child = spawn(binary, args, { stdio: ["ignore", "ignore", "pipe"] });
  let stderr = "";
  child.stderr?.on("data", (chunk: Buffer) => {
    stderr = (stderr + chunk.toString()).slice(-4000);
  });
  const exited = new Promise<void>((resolve) => child.once("exit", () => resolve()));

  const portFile = `${options.profile}/DevToolsActivePort`;
  const deadline = Date.now() + (options.timeoutMs ?? 20_000);
  while (!existsSync(portFile) || readFileSync(portFile, "utf8").trim() === "") {
    if (child.exitCode !== null) throw new Error(`Chrome exited (${child.exitCode}): ${stderr.trim()}`);
    if (Date.now() > deadline) {
      child.kill();
      throw new Error(`Chrome didn't open its DevTools port: ${stderr.trim()}`);
    }
    await sleep(50);
  }
  const port = Number(readFileSync(portFile, "utf8").split("\n")[0]);
  return {
    port,
    process: child,
    close: async () => {
      if (child.exitCode === null) child.kill();
      // At most 5 s for Chrome to go, and no timer left behind to keep Node running.
      const waited = new AbortController();
      await Promise.race([exited, sleep(5000, undefined, { signal: waited.signal }).catch(() => {})]);
      waited.abort();
    },
  };
}

type Handler = (params: Record<string, unknown>) => void;

interface Pending {
  method: string;
  resolve: (value: unknown) => void;
  reject: (error: Error) => void;
}

/** One DevTools connection (a page's): numbered commands and their replies, and events. */
export class Cdp {
  private readonly socket: WebSocket;
  private readonly pending = new Map<number, Pending>();
  private readonly handlers = new Map<string, Set<Handler>>();
  private next = 0;

  constructor(socket: WebSocket) {
    this.socket = socket;
    socket.addEventListener("message", (event) => this.receive(String(event.data)));
    socket.addEventListener("close", () => {
      for (const call of this.pending.values()) call.reject(new Error(`${call.method}: the DevTools connection closed`));
      this.pending.clear();
    });
  }

  /** Opens the first page target of a Chrome started by `launchChrome`. */
  static async page(port: number): Promise<Cdp> {
    const targets = (await (await fetch(`http://127.0.0.1:${port}/json/list`)).json()) as {
      type: string;
      webSocketDebuggerUrl?: string;
    }[];
    const url = targets.find((t) => t.type === "page")?.webSocketDebuggerUrl;
    if (!url) throw new Error("Chrome has no page target");
    const socket = new WebSocket(url);
    await new Promise<void>((resolve, reject) => {
      socket.addEventListener("open", () => resolve(), { once: true });
      socket.addEventListener("error", () => reject(new Error(`couldn't connect to ${url}`)), { once: true });
    });
    return new Cdp(socket);
  }

  private receive(text: string): void {
    const message = JSON.parse(text) as {
      id?: number;
      result?: unknown;
      error?: { message: string; data?: string };
      method?: string;
      params?: Record<string, unknown>;
    };
    if (message.id !== undefined) {
      const call = this.pending.get(message.id);
      if (!call) return;
      this.pending.delete(message.id);
      if (message.error) call.reject(new Error(`${call.method}: ${message.error.message}${message.error.data ? ` (${message.error.data})` : ""}`));
      else call.resolve(message.result ?? {});
    } else if (message.method) {
      for (const handler of this.handlers.get(message.method) ?? []) handler(message.params ?? {});
    }
  }

  /** Sends one command and resolves with its result (rejects with the protocol's error). */
  send<T = Record<string, unknown>>(method: string, params: Record<string, unknown> = {}): Promise<T> {
    const id = ++this.next;
    return new Promise<T>((resolve, reject) => {
      this.pending.set(id, { method, resolve: resolve as (value: unknown) => void, reject });
      this.socket.send(JSON.stringify({ id, method, params }));
    });
  }

  /** Calls `handler` on every `method` event; returns the unsubscribe. */
  on(method: string, handler: Handler): () => void {
    const set = this.handlers.get(method) ?? new Set<Handler>();
    set.add(handler);
    this.handlers.set(method, set);
    return () => set.delete(handler);
  }

  /**
   * Evaluates an expression in the page and returns its value (JSON-serialisable values only).
   * A promise is awaited. A thrown error rejects with the page's own message.
   */
  async evaluate<T = unknown>(expression: string): Promise<T> {
    const reply = await this.send<{
      result: { value?: unknown };
      exceptionDetails?: { text: string; exception?: { description?: string } };
    }>("Runtime.evaluate", { expression, awaitPromise: true, returnByValue: true, userGesture: true });
    if (reply.exceptionDetails) {
      const { text, exception } = reply.exceptionDetails;
      throw new Error(exception?.description?.split("\n")[0] ?? text);
    }
    return reply.result.value as T;
  }

  /** Polls a page expression until it is truthy; its value, or a timeout error naming `what`. */
  async waitFor<T = unknown>(expression: string, what: string, timeoutMs: number, pollMs = 100): Promise<T> {
    const deadline = Date.now() + timeoutMs;
    for (;;) {
      let value: T | undefined;
      try {
        value = await this.evaluate<T>(expression);
      } catch {
        value = undefined; // the page is navigating: its context went away mid-call
      }
      if (value) return value;
      if (Date.now() > deadline) throw new Error(`timed out after ${Math.round(timeoutMs / 1000)} s waiting for ${what}`);
      await sleep(pollMs);
    }
  }

  /** The viewport as a JPEG, base64-encoded. */
  async screenshot(quality: number): Promise<string> {
    const reply = await this.send<{ data: string }>("Page.captureScreenshot", { format: "jpeg", quality, optimizeForSpeed: true });
    return reply.data;
  }

  /** A real mouse click (move, press, release) at a viewport point, so hover states show too. */
  async click(x: number, y: number): Promise<void> {
    await this.send("Input.dispatchMouseEvent", { type: "mouseMoved", x, y });
    await this.send("Input.dispatchMouseEvent", { type: "mousePressed", x, y, button: "left", clickCount: 1 });
    await this.send("Input.dispatchMouseEvent", { type: "mouseReleased", x, y, button: "left", clickCount: 1 });
  }

  /** Types text into the focused element, as an input method would (one `input` event). */
  async insertText(text: string): Promise<void> {
    await this.send("Input.insertText", { text });
  }

  close(): void {
    this.socket.close();
  }
}
