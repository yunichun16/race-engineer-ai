/**
 * Records the demo video (plan 12): plays the storyboard (storyboard.ts) on the running site in
 * headless Google Chrome, captures frames over the DevTools protocol (cdp.ts), then has
 * encode.swift write an H.264 MP4 with the captions drawn on. No packages, no ffmpeg.
 *
 *   node scripts/demo/record.ts [--site http://localhost:3000] [--api http://127.0.0.1:8000]
 *        [--out ../data/demo] [--label race-engineer.vercel.app] [--skip chat,styles]
 *        [--bitrate 2500000] [--keep-frames] [--no-encode]                    (or make demo-video)
 *
 * `--site` is the site to film; `--api` is the API that site calls (its NEXT_PUBLIC_API_URL),
 * checked before recording. With the scripted chat (a local draft) the chat scene's caption
 * doesn't name Claude; against a real chat the scene asks one real question (about $0.02, and
 * one of the visitor's questions that hour). `--label` is the address the end card shows (the
 * site's host by default). `--skip` leaves scenes out, for instance `chat` when the hour's
 * questions are used up, or every scene but `end` for the end card alone (docs/demo.md's
 * QuickTime recording ends on it).
 *
 * It writes only under `--out` (default the repository's data/demo/, gitignored): the frames and
 * their timing file (deleted after a good encode unless --keep-frames), captions.json, stills
 * of each scene from the finished file, the throwaway Chrome profile (deleted at the end), and
 * race-engineer-demo.mp4. It exits with 1 when a page doesn't show what a step waits for (with a
 * screenshot of the page as it was) or the video can't be written.
 */

import { spawnSync } from "node:child_process";
import { mkdirSync, rmSync, writeFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { setTimeout as sleep } from "node:timers/promises";
import { captionTrack, ms, problems, timeline, type Frame, type SceneTiming } from "./captions.ts";
import { Cdp, launchChrome, type Chrome } from "./cdp.ts";
import { endCard, onCameraLength, plannedLength, POINTER, STORYBOARD, type EndCard, type Scene, type Step } from "./storyboard.ts";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const WIDTH = 1280;
const HEIGHT = 720;
const OUTPUT_FPS = 30;
const SCREENCAST_NTH = 2; // every second compositor frame: 30 a second at 60 Hz
const JPEG_QUALITY = 90;
const GOTO_TIMEOUT_MS = 120_000; // `next dev` compiles a route on its first visit
const READY_TIMEOUT_MS = 90_000; // and a cold API loads a whole session for a corner
const CLICK_WAIT_S = 5; // a button may take a moment to be enabled (Send, once the draft is in)
const VIDEO = "race-engineer-demo.mp4";

// ---- Arguments ----------------------------------------------------------------------------------

/** An option's value; the fallback when it's missing or empty (`make demo-video` with no SITE). */
function option(argv: string[], name: string, fallback: string): string {
  const i = argv.indexOf(name);
  const given = i >= 0 ? argv[i + 1] : undefined;
  return given && !given.startsWith("--") ? given : fallback;
}

const argv = process.argv.slice(2);
const SITE = option(argv, "--site", "http://localhost:3000").replace(/\/+$/, "");
const API = option(argv, "--api", "http://127.0.0.1:8000").replace(/\/+$/, "");
const OUT = path.resolve(option(argv, "--out", path.resolve(HERE, "../../../data/demo")));
const LABEL = option(argv, "--label", "") || undefined;
const SKIP = new Set(option(argv, "--skip", "").split(",").map((s) => s.trim()).filter(Boolean));
const BITRATE = option(argv, "--bitrate", "2500000");
const KEEP_FRAMES = argv.includes("--keep-frames");
const ENCODE = !argv.includes("--no-encode");
const CHROME_BINARY = option(argv, "--chrome", "") || undefined;

const FRAMES = path.join(OUT, "frames");
const PROFILE = path.join(OUT, "chrome-profile");
const STILLS = path.join(OUT, "stills");

function log(message: string): void {
  console.log(`[demo] ${message}`);
}

// ---- Checks before recording --------------------------------------------------------------------

/**
 * The API's chat mode ("fake" for the scripted chat), or exit with why the API isn't usable. A
 * chat that takes no questions now (off, paused, today's cap) is caught here, not after the chat
 * scene has waited out its timeouts.
 */
async function checkApi(): Promise<string> {
  let body: { status?: string; chat?: { mode?: string; available?: boolean; reason?: string | null } };
  try {
    const response = await fetch(`${API}/api/health`, { signal: AbortSignal.timeout(20_000) });
    body = (await response.json()) as typeof body;
  } catch (error) {
    throw new Error(`the API isn't answering at ${API}/api/health (${error instanceof Error ? error.message : error})`);
  }
  if (body.status !== "ok") throw new Error(`the API at ${API} reports status "${body.status}", not "ok"`);
  if (!SKIP.has("chat") && body.chat?.available === false) {
    throw new Error(`the API's chat isn't taking questions (${body.chat.reason ?? "unavailable"}): try later, or --skip chat`);
  }
  return body.chat?.mode ?? "unknown";
}

async function checkSite(): Promise<void> {
  const response = await fetch(`${SITE}/`, { signal: AbortSignal.timeout(GOTO_TIMEOUT_MS) }).catch((error: unknown) => {
    throw new Error(`the site isn't answering at ${SITE} (${error instanceof Error ? error.message : error})`);
  });
  if (response.status !== 200) throw new Error(`the site's landing page answered HTTP ${response.status}`);
}

// ---- The recorder -------------------------------------------------------------------------------

/**
 * Captures frames while it is rolling, from Chrome's screencast: the compositor sends a frame
 * whenever the page repaints (every second one at 60 Hz, so up to 30 a second), stamped with when
 * it was drawn; nothing comes while the page is still. Each frame is placed at its time in the
 * video. Paused, the video's clock stops, so whatever happens off camera takes no time on screen.
 * Rolling starts with a screenshot of the view as it is, since a still page sends no frame. A
 * frame identical to the one before isn't stored again.
 *
 * (A screenshot loop manages only about 12 frames a second while the page scrolls: each capture
 * waits for a fresh frame of its own. P5 measured the screencast at 60.)
 */
class Recorder {
  readonly frames: Frame[] = [];
  private readonly page: Cdp;
  private offset = 0; // the video's time when the recorder last paused
  private rollingSince: number | null = null; // wall clock (ms since the epoch) when it last started rolling
  private last = "";
  private count = 0;

  constructor(page: Cdp) {
    this.page = page;
    page.on("Page.screencastFrame", (params) => {
      void page.send("Page.screencastFrameAck", { sessionId: params.sessionId as number }).catch(() => {});
      const drawn = (params.metadata as { timestamp?: number } | undefined)?.timestamp;
      if (this.rollingSince === null || drawn === undefined) return;
      const since = drawn * 1000 - this.rollingSince;
      if (since < 0) return; // drawn before rolling: the opening screenshot shows this
      this.store(params.data as string, this.offset + since / 1000);
    });
  }

  async start(): Promise<void> {
    await this.page.send("Page.startScreencast", {
      format: "jpeg",
      quality: JPEG_QUALITY,
      maxWidth: WIDTH,
      maxHeight: HEIGHT,
      everyNthFrame: SCREENCAST_NTH,
    });
  }

  async stop(): Promise<void> {
    await this.page.send("Page.stopScreencast").catch(() => {});
  }

  get rolling(): boolean {
    return this.rollingSince !== null;
  }

  /** The video's time now, in seconds. */
  get now(): number {
    return this.rollingSince === null ? this.offset : this.offset + (Date.now() - this.rollingSince) / 1000;
  }

  async roll(): Promise<void> {
    if (this.rolling) return;
    const opening = await this.page.screenshot(JPEG_QUALITY);
    this.rollingSince = Date.now();
    this.store(opening, this.offset);
  }

  pause(): void {
    if (!this.rolling) return;
    this.offset = this.now;
    this.rollingSince = null;
  }

  private store(data: string, t: number): void {
    if (data === this.last) return;
    const file = `${String(this.count++).padStart(6, "0")}.jpg`;
    writeFileSync(path.join(FRAMES, file), Buffer.from(data, "base64"));
    this.frames.push({ t: ms(t), file });
    this.last = data;
  }

  /** Rolls until the video's clock reaches `t`. */
  async until(t: number): Promise<void> {
    await this.roll();
    for (;;) {
      const left = t - this.now;
      if (left <= 0) return;
      await sleep(Math.min(left * 1000, 50));
    }
  }
}

// ---- In the page --------------------------------------------------------------------------------
// These run in the browser: each is sent as its source (Node strips the types and keeps the
// positions), so it must not use anything from this module's scope.

/** Before any page script: the Dark theme (the site's default anyway) and no Next.js dev badge. */
const PRELUDE = `(() => {
  try { localStorage.setItem("re-theme", "dark"); } catch {}
  const style = () => {
    const s = document.createElement("style");
    s.textContent = "nextjs-portal { display: none !important; }";
    document.head.append(s);
  };
  if (document.head) style(); else document.addEventListener("DOMContentLoaded", style, { once: true });
})()`;

/**
 * Scrolls smoothly (ease in and out) to the top, a target's top under the header, or the story
 * band. It scrolls whatever holds the target: the page, or a scrolling column such as the chat's
 * transcript.
 */
function pageScroll(target: Element | null, block: "top" | "start" | "story", seconds: number): Promise<number> {
  const page = document.scrollingElement ?? document.documentElement;
  let box: Element = page;
  for (let el = target?.parentElement ?? null; el; el = el.parentElement) {
    const overflow = getComputedStyle(el).overflowY;
    if ((overflow === "auto" || overflow === "scroll") && el.scrollHeight > el.clientHeight + 1) {
      box = el;
      break;
    }
  }
  const isPage = box === page;
  const header = document.querySelector("header")?.getBoundingClientRect().bottom ?? 64;
  const top = isPage ? 0 : box.getBoundingClientRect().top;
  const height = isPage ? window.innerHeight : box.clientHeight;
  let y = 0;
  if (block !== "top") {
    if (!target) throw new Error("the scroll target isn't on the page");
    const rect = target.getBoundingClientRect();
    // "story": the target's middle at 55% of the viewport, inside the band (50-60%) where the
    // landing story's IntersectionObserver picks the active step.
    y =
      block === "story"
        ? box.scrollTop + rect.top - top + rect.height / 2 - height * 0.55
        : box.scrollTop + rect.top - top - (isPage ? header + 20 : 16);
  }
  const to = Math.max(0, Math.min(box.scrollHeight - height, y));
  const from = box.scrollTop;
  const start = performance.now();
  const ease = (x: number) => (x < 0.5 ? 4 * x * x * x : 1 - Math.pow(-2 * x + 2, 3) / 2);
  return new Promise((resolve) => {
    const frame = (now: number) => {
      const p = Math.min(1, (now - start) / (seconds * 1000));
      box.scrollTo({ top: from + (to - from) * ease(p), behavior: "instant" });
      if (p < 1) requestAnimationFrame(frame);
      else resolve(box.scrollTop);
    };
    requestAnimationFrame(frame);
  });
}

/** Where a target is (its middle, in viewport pixels), whether it's in view and whether it's enabled. */
function pageBox(target: Element | null): { x: number; y: number; inView: boolean; enabled: boolean } | null {
  if (!target) return null;
  const box = target.getBoundingClientRect();
  const x = box.left + box.width / 2;
  const y = box.top + box.height / 2;
  const header = document.querySelector("header")?.getBoundingClientRect().bottom ?? 64;
  return {
    x,
    y,
    inView: y > header + 8 && y < window.innerHeight - 24 && box.width > 0,
    enabled: !(target as HTMLButtonElement).disabled && target.getAttribute("aria-disabled") !== "true",
  };
}

/**
 * A pointer drawn over the page, since headless Chrome shows none: a soft ring in the text colour
 * that glides to a point (and pulses when `press`). Site tokens only, so it matches the theme.
 */
function pagePointer(x: number, y: number, press: boolean): void {
  let ring = document.getElementById("re-demo-pointer");
  if (!ring) {
    ring = document.createElement("div");
    ring.id = "re-demo-pointer";
    ring.setAttribute("aria-hidden", "true");
    Object.assign(ring.style, {
      position: "fixed",
      left: "0",
      top: "0",
      width: "28px",
      height: "28px",
      margin: "-14px 0 0 -14px",
      borderRadius: "50%",
      border: "2px solid var(--color-text-primary)",
      background: "color-mix(in srgb, var(--color-text-primary) 22%, transparent)",
      boxShadow: "var(--shadow-glass-sm)",
      pointerEvents: "none",
      zIndex: "2147483647",
      opacity: "0",
      transform: `translate(${Math.round(window.innerWidth * 0.62)}px, ${Math.round(window.innerHeight * 0.8)}px)`,
      // The glide (550 ms) ends before the click comes, POINTER.glideS after it starts.
      transition: "transform 550ms var(--ease-out), opacity 240ms ease-out, scale 160ms ease-out",
    });
    document.body.append(ring);
    void ring.offsetWidth; // start the first glide from the resting point
  }
  ring.style.opacity = "1";
  ring.style.transform = `translate(${Math.round(x)}px, ${Math.round(y)}px)`;
  if (press) {
    ring.style.scale = "0.72";
    window.setTimeout(() => {
      if (ring) ring.style.scale = "1";
    }, 180);
  }
}

/** The end card over the page: the site's logo (the header's glowing dot), its name and address, and the small print. */
function pageEndCard(card: EndCard): void {
  const make = (tag: string, text: string, style: Partial<CSSStyleDeclaration>) => {
    const el = document.createElement(tag);
    el.textContent = text;
    Object.assign(el.style, style);
    return el;
  };
  const root = document.createElement("div");
  root.id = "re-demo-end";
  Object.assign(root.style, {
    position: "fixed",
    inset: "0",
    zIndex: "2147483646",
    display: "grid",
    placeItems: "center",
    background: "color-mix(in srgb, var(--page) 90%, transparent)",
    backdropFilter: "blur(22px) saturate(1.2)",
    opacity: "0",
    transition: "opacity 900ms var(--ease-out)",
    fontFamily: "var(--font-sans)",
    color: "var(--color-text-primary)",
    textAlign: "center",
  });
  const column = document.createElement("div");
  Object.assign(column.style, { display: "grid", justifyItems: "center", gap: "14px", maxWidth: "900px" });

  const logo = document.querySelector("header a svg")?.cloneNode(true) as SVGElement | undefined;
  if (logo) {
    logo.removeAttribute("class");
    Object.assign(logo.style, { width: "64px", height: "64px", overflow: "visible", filter: "drop-shadow(0 0 10px var(--accent))", marginBottom: "10px" });
    column.append(logo);
  }
  column.append(
    make("div", card.name, { fontSize: "68px", fontWeight: "600", letterSpacing: "-0.02em", lineHeight: "1.02" }),
    make("div", card.address, { fontFamily: "var(--font-mono)", fontSize: "24px", letterSpacing: "0.02em", marginTop: "6px" }),
    make("div", card.connector, { fontSize: "21px", color: "var(--color-text-secondary)" }),
    make("div", "", { width: "120px", height: "1px", background: "var(--color-border-tertiary)", margin: "18px 0 6px" }),
    make("div", card.fanProject, { fontSize: "18px", fontWeight: "500" }),
  );
  if (card.credit) {
    column.append(make("div", card.credit, { fontSize: "16px", color: "var(--color-text-secondary)" }));
  }
  root.append(column);
  document.body.append(root);
  void root.offsetWidth;
  root.style.opacity = "1";
  // The pointer goes with the page: nothing on the card is to be clicked.
  const pointer = document.getElementById("re-demo-pointer");
  if (pointer) pointer.style.opacity = "0";
}

/** Calls an in-page function with a target expression and JSON arguments; its result. */
function call(fn: (...args: never[]) => unknown, target: string | null, ...args: unknown[]): string {
  const rest = args.map((a) => JSON.stringify(a)).join(", ");
  const first = target === null ? "null" : `(() => (${target}))()`;
  return `(${fn.toString()})(${first}${rest ? `, ${rest}` : ""})`;
}

function callPlain(fn: (...args: never[]) => unknown, ...args: unknown[]): string {
  return `(${fn.toString()})(${args.map((a) => JSON.stringify(a)).join(", ")})`;
}

// ---- Playing the storyboard ---------------------------------------------------------------------

class StepFailed extends Error {}

/** Where a scene that opens no page plays when nothing is open yet (see `record`). */
const LANDING: Extract<Step, { kind: "goto" }> = {
  kind: "goto",
  path: "/",
  ready: `!!document.querySelector("header a svg")`,
  what: "the landing page",
};

async function goto(page: Cdp, step: Extract<Step, { kind: "goto" }>): Promise<void> {
  let loaded = () => {};
  const load = new Promise<void>((resolve) => (loaded = resolve));
  const off = page.on("Page.loadEventFired", () => loaded());
  // Cleared once the page loads: a pending timer would keep Node running after the video is done.
  let timer: ReturnType<typeof setTimeout> | undefined;
  const late = new Promise<never>((_, reject) => {
    timer = setTimeout(() => reject(new StepFailed(`${step.path} didn't finish loading in ${GOTO_TIMEOUT_MS / 1000} s`)), GOTO_TIMEOUT_MS);
  });
  try {
    const reply = await page.send<{ errorText?: string }>("Page.navigate", { url: `${SITE}${step.path}` });
    if (reply.errorText) throw new StepFailed(`couldn't open ${step.path}: ${reply.errorText}`);
    await Promise.race([load, late]);
  } finally {
    clearTimeout(timer);
    off();
  }
  await page.waitFor(`!!(${step.ready})`, step.what, READY_TIMEOUT_MS).catch((error: Error) => {
    throw new StepFailed(error.message);
  });
  await sleep(300); // let the first paint after the data settle
}

/** Waits until `until` holds: on camera for up to `live` seconds, then off camera. */
async function waitStep(page: Cdp, rec: Recorder, step: Extract<Step, { kind: "wait" }>): Promise<void> {
  const check = async () => Boolean(await page.evaluate(`!!(${step.until})`).catch(() => false));
  const timeout = (step.timeoutS ?? READY_TIMEOUT_MS / 1000) * 1000;
  const started = performance.now();
  if (step.live) {
    await rec.roll();
    const liveUntil = rec.now + step.live;
    while (rec.now < liveUntil) {
      if (await check()) return;
      await sleep(100);
    }
    rec.pause(); // a cut: the rest of the wait doesn't show
  }
  while (!(await check())) {
    if (performance.now() - started > timeout) throw new StepFailed(`timed out after ${timeout / 1000} s waiting for ${step.what}`);
    await sleep(150);
  }
}

async function scrollStep(page: Cdp, target: string | null, block: "top" | "start" | "story", seconds: number): Promise<void> {
  await page.evaluate(call(pageScroll, target, block, seconds)).catch((error: Error) => {
    throw new StepFailed(`scroll: ${error.message}`);
  });
}

async function click(page: Cdp, rec: Recorder, target: string, what: string): Promise<void> {
  const deadline = rec.now + CLICK_WAIT_S;
  let box = await page.evaluate<ReturnType<typeof pageBox>>(call(pageBox, target));
  while (!box || !box.enabled) {
    if (rec.now > deadline) throw new StepFailed(box ? `${what} stayed disabled` : `no ${what} on the page`);
    await sleep(100);
    box = await page.evaluate<ReturnType<typeof pageBox>>(call(pageBox, target));
  }
  if (!box.inView) {
    await scrollStep(page, target, "story", 0.7);
    box = (await page.evaluate<ReturnType<typeof pageBox>>(call(pageBox, target))) ?? box;
  }
  await page.evaluate(callPlain(pagePointer, box.x, box.y, false));
  await sleep(POINTER.glideS * 1000);
  await page.evaluate(callPlain(pagePointer, box.x, box.y, true));
  await page.click(box.x, box.y);
  await sleep(POINTER.pressS * 1000);
}

async function typeStep(page: Cdp, rec: Recorder, step: Extract<Step, { kind: "type" }>): Promise<void> {
  await click(page, rec, step.target, "the text box");
  const each = (step.s * 1000) / step.text.length;
  const start = performance.now();
  for (let i = 0; i < step.text.length; i++) {
    await page.insertText(step.text[i]);
    const wait = start + (i + 1) * each - performance.now();
    if (wait > 0) await sleep(wait);
  }
}

async function playStep(page: Cdp, rec: Recorder, step: Step, card: EndCard): Promise<void> {
  switch (step.kind) {
    case "goto":
      return goto(page, step);
    case "wait":
      return waitStep(page, rec, step);
    case "hold":
      return rec.until(rec.now + step.s);
    case "scroll":
      return scrollStep(page, step.to === "top" ? null : step.to, step.to === "top" ? "top" : (step.block ?? "start"), step.s);
    case "click":
      return click(page, rec, step.target, step.what);
    case "type":
      return typeStep(page, rec, step);
    case "endcard":
      await page.evaluate(callPlain(pageEndCard, card));
      return;
  }
}

/** Off-camera steps pause the recorder; every other step rolls it. */
function onCamera(step: Step): boolean {
  return step.kind !== "goto" && !(step.kind === "wait" && !step.live);
}

async function playScene(page: Cdp, rec: Recorder, scene: Scene, card: EndCard): Promise<SceneTiming> {
  let start: number | null = null;
  for (const step of scene.steps) {
    if (onCamera(step)) {
      start ??= rec.now; // read while paused: exactly where the scene before ended
      await rec.roll();
    } else {
      rec.pause();
    }
    try {
      await playStep(page, rec, step, card);
    } catch (error) {
      const why = error instanceof Error ? error.message : String(error);
      throw new StepFailed(`scene "${scene.id}", step ${scene.steps.indexOf(step) + 1} (${step.kind}): ${why}`);
    }
  }
  start ??= rec.now;
  const end = start + scene.s;
  if (rec.now > end + 0.05) log(`scene "${scene.id}" ran ${(rec.now - end).toFixed(1)} s over its ${scene.s} s`);
  await rec.until(end);
  rec.pause();
  return { id: scene.id, start, end: rec.now };
}

// ---- Main ---------------------------------------------------------------------------------------

async function record(chatMode: string): Promise<{ frames: Frame[]; scenes: SceneTiming[] }> {
  rmSync(FRAMES, { recursive: true, force: true });
  rmSync(PROFILE, { recursive: true, force: true });
  mkdirSync(FRAMES, { recursive: true });
  let chrome: Chrome | null = null;
  let page: Cdp | null = null;
  try {
    chrome = await launchChrome({ binary: CHROME_BINARY, profile: PROFILE, width: WIDTH, height: HEIGHT });
    page = await Cdp.page(chrome.port);
    await page.send("Page.enable");
    await page.send("Emulation.setDeviceMetricsOverride", { width: WIDTH, height: HEIGHT, deviceScaleFactor: 1, mobile: false });
    await page.send("Emulation.setEmulatedMedia", {
      features: [
        { name: "prefers-color-scheme", value: "dark" },
        { name: "prefers-reduced-motion", value: "no-preference" },
      ],
    });
    await page.send("Page.addScriptToEvaluateOnNewDocument", { source: PRELUDE });

    const card = endCard(SITE, LABEL);
    const rec = new Recorder(page);
    await rec.start();
    const scenes: SceneTiming[] = [];
    let opened = false;
    for (const scene of STORYBOARD) {
      if (SKIP.has(scene.id)) continue;
      const started = performance.now();
      // A scene that opens no page of its own (the end card) plays over the landing page when
      // nothing is open yet: `--skip` everything else to film the end card alone.
      if (!opened && scene.steps[0]?.kind !== "goto") await goto(page, LANDING);
      opened = true;
      const timing = await playScene(page, rec, scene, card);
      scenes.push(timing);
      log(`${scene.id.padEnd(8)} ${timing.start.toFixed(2)}-${timing.end.toFixed(2)} s on screen, ${((performance.now() - started) / 1000).toFixed(1)} s to film`);
    }
    await rec.stop();
    if (chatMode !== "fake" && !SKIP.has("chat")) log("the chat scene asked the real model one question");
    return { frames: rec.frames, scenes };
  } catch (error) {
    if (page && error instanceof StepFailed) {
      const shot = await page.screenshot(85).catch(() => null);
      if (shot) {
        const file = path.join(OUT, "failed.jpg");
        writeFileSync(file, Buffer.from(shot, "base64"));
        log(`the page as it was: ${file}`);
      }
    }
    throw error;
  } finally {
    page?.close();
    await chrome?.close();
    rmSync(PROFILE, { recursive: true, force: true });
  }
}

function encode(captionsFile: string, out: string): Record<string, unknown> {
  const swift = spawnSync(
    "swift",
    [path.join(HERE, "encode.swift"), FRAMES, captionsFile, out, "--bitrate", BITRATE, "--stills", STILLS],
    { encoding: "utf8", stdio: ["ignore", "pipe", "inherit"], maxBuffer: 1 << 20 },
  );
  if (swift.error) throw new Error(`couldn't run swift: ${swift.error.message}`);
  if (swift.status !== 0) throw new Error(`encode.swift exited with ${swift.status}`);
  const last = swift.stdout.trim().split("\n").at(-1) ?? "";
  return JSON.parse(last) as Record<string, unknown>;
}

async function main(): Promise<void> {
  const unknown = [...SKIP].filter((id) => !STORYBOARD.some((scene) => scene.id === id));
  if (unknown.length) throw new Error(`--skip: no scene ${unknown.join(", ")} (scenes: ${STORYBOARD.map((s) => s.id).join(", ")})`);
  for (const scene of STORYBOARD) {
    const most = onCameraLength(scene);
    if (most > scene.s) log(`scene "${scene.id}" can take ${most} s on camera, more than its ${scene.s} s: the video may run long`);
  }

  const chatMode = await checkApi();
  await checkSite();
  log(`filming ${SITE} (API ${API}, chat ${chatMode}), about ${plannedLength(STORYBOARD, SKIP)} s, into ${OUT}`);
  if (chatMode === "fake" && !SKIP.has("chat")) log("the scripted chat: a draft (its caption doesn't name Claude)");
  mkdirSync(OUT, { recursive: true });
  rmSync(path.join(OUT, "failed.jpg"), { force: true });

  const filmed = performance.now();
  const { frames, scenes } = await record(chatMode);
  const line = timeline({ width: WIDTH, height: HEIGHT, fps: OUTPUT_FPS, frames, scenes });
  const captions = captionTrack(line.scenes, STORYBOARD, chatMode === "fake");
  const found = problems(line, captions);
  writeFileSync(path.join(FRAMES, "timing.json"), JSON.stringify(line, null, 1));
  const captionsFile = path.join(OUT, "captions.json");
  writeFileSync(captionsFile, JSON.stringify(captions, null, 2));
  log(`${frames.length} distinct frames over ${line.duration.toFixed(2)} s, filmed in ${((performance.now() - filmed) / 1000).toFixed(0)} s`);
  if (found.length) throw new Error(`the recording can't be encoded: ${found.join("; ")}`);
  if (!ENCODE) {
    log(`not encoding (--no-encode): ${path.join(FRAMES, "timing.json")} and ${captionsFile}`);
    return;
  }

  rmSync(STILLS, { recursive: true, force: true });
  const out = path.join(OUT, VIDEO);
  const video = encode(captionsFile, out);
  log(
    `${out}: ${video.duration_s} s, ${video.width} × ${video.height}, ${video.codec}, ` +
      `${(Number(video.bytes) / 1024 / 1024).toFixed(1)} MB, encoded in ${video.encode_s} s; stills in ${STILLS}`,
  );
  console.log(JSON.stringify(video));
  if (!KEEP_FRAMES) rmSync(FRAMES, { recursive: true, force: true });
}

main().catch((error: unknown) => {
  console.error(`[demo] ${error instanceof Error ? error.message : String(error)}`);
  process.exit(1);
});
