/**
 * The demo video's path through the site (plan 12): seven scenes of about 75 seconds in all, each
 * with its caption. `record.ts` plays it in headless Chrome; docs/demo.md has the same storyboard
 * as a click-by-click script for a QuickTime recording.
 *
 * Steps that go somewhere (`goto`) or wait for something (`wait`) happen off camera, so a slow
 * first compile under `next dev`, a cold API or a long chat answer never shows: the video cuts
 * over them. A `wait` with `live` keeps recording for up to that many seconds first (an answer
 * streaming in), then cuts. Everything else is on camera. After its steps a scene holds until it
 * has run its length `s`.
 *
 * Targets are page expressions that find one element (the builders below), so the storyboard
 * waits on what the page shows, never on fixed sleeps.
 *
 * Every scene's steps fit its length even when each `live` wait runs to the end (a real model
 * answering, a cold API): `onCameraLength` adds them up and captions.test.ts holds each scene to
 * it, so the video stays at its planned length whichever chat answers.
 */

import { site } from "../../src/content/site.ts";

/** A page expression that evaluates to one element, or null while it isn't there. */
export type Target = string;

export type Step =
  /** Off camera: load a path of the site and wait until `ready` is truthy. */
  | { kind: "goto"; path: string; ready: string; what: string }
  /** Off camera (after `live` seconds on camera, if given): wait until `until` is truthy. */
  | { kind: "wait"; until: string; what: string; live?: number; timeoutS?: number }
  | { kind: "hold"; s: number }
  /**
   * Scroll smoothly over `s` seconds: to the top, or to put the target's top under the header
   * ("start") or its middle in the band where the landing story picks its step ("story").
   */
  | { kind: "scroll"; to: Target | "top"; block?: "start" | "story"; s: number }
  /** Glide the pointer to the target and click it (waits up to a few seconds for it to be enabled). */
  | { kind: "click"; target: Target; what: string }
  /** Click into the target, then type the text over `s` seconds. */
  | { kind: "type"; target: Target; text: string; s: number }
  /** Fade the end card in over the page. */
  | { kind: "endcard" };

/** How a click looks on camera: the drawn pointer glides to the target, then presses it. */
export const POINTER = { glideS: 0.65, pressS: 0.25 } as const;

export interface Scene {
  id: string;
  /** The caption drawn over the scene; null for none (the end card carries its own words). */
  caption: string | null;
  /** The caption when the API's chat is the scripted one (a local draft), where it differs. */
  scriptedCaption?: string;
  /** The scene's length on screen, in seconds. */
  s: number;
  steps: Step[];
}

// ---- Targets ------------------------------------------------------------------------------------

const json = (text: string) => JSON.stringify(text);

/** The first element matching a CSS selector. */
export const css = (selector: string): Target => `document.querySelector(${json(selector)})`;

/** A button whose accessible name (aria-label, else its text) starts with `name`. */
export const button = (name: string, within = "document"): Target =>
  `[...${within}.querySelectorAll("button")].find((b) => (b.getAttribute("aria-label") || b.textContent || "").trim().startsWith(${json(name)})) ?? null`;

/** An h1-h4 whose text starts with `text`. */
export const heading = (text: string): Target =>
  `[...document.querySelectorAll("h1,h2,h3,h4")].find((h) => (h.textContent || "").trim().startsWith(${json(text)})) ?? null`;

/** A segmented control's option (a native radio inside its label) whose text is `text`. */
export const choice = (text: string): Target =>
  `[...document.querySelectorAll("label")].find((l) => (l.textContent || "").trim() === ${json(text)} && l.querySelector("input[type=radio]")) ?? null`;

/** A drawn chart of one kind (charts/styles/base.css: `.re-chart--<name>` holds its svg once drawn). */
const chart = (name: string): Target => css(`.re-chart--${name} svg`);

/** True when the expression finds something. */
const has = (target: Target) => `!!(${target})`;

/** The first target, else the second: a pinned row with a fallback if the data has moved on. */
const either = (first: Target, second: Target): Target => `(${first}) ?? (${second})`;

// ---- What the scenes look for -------------------------------------------------------------------

/** The question the chat scene types: one of the chat's own chips (content/questions.ts). */
export const QUESTION = "Who made the biggest mistakes in the last race?";

/** The story's figure caption names the tool once the featured corner's data has arrived. */
const STORY_DATA = `(document.querySelector('[aria-labelledby="story-title"] figcaption')?.textContent || "").includes("from explain_corner")`;

/** The landing's real chart (ExampleCorner), and its replay. */
const STORY_CHART = css('[aria-labelledby="story-title"] .re-chart--explain-corner');
const STORY_REPLAY = css('[aria-labelledby="story-title"] .re-chart--explain-corner .replay-panel');

/** An answer is finished when its chart is drawn and the composer offers Send again, not Stop. */
const ANSWER_DONE = `${has(chart("find-mistakes"))} && !${has(button("Stop"))}`;

/** A featured corner of a pinned session (content/featured.ts), with any row of it as the fallback. */
const MISTAKES_PATH = "/mistakes?year=2026&event=Spanish+Grand+Prix&session=R";
const MISTAKES_ROW = either(button("Show telemetry for ALB's lap 55, turn 3"), button("Show telemetry"));

const STYLES_PATH = "/styles?a=HAM&b=LEC&year=2025";

const DETECTORS = `(${heading("The baseline ranks better overall")})?.closest("figure") ?? null`;

// ---- The scenes ---------------------------------------------------------------------------------

export const STORYBOARD: readonly Scene[] = [
  {
    id: "hero",
    caption: "Deep learning on F1 telemetry finds the corners where drivers lost time.",
    s: 7,
    steps: [
      { kind: "goto", path: "/", ready: has(css('[aria-roledescription="slide"]')), what: "the hero's real examples" },
      { kind: "hold", s: 7 },
    ],
  },
  {
    id: "story",
    caption: "Each flagged corner is explained against the driver's usual lap, with a replay of the cars around.",
    s: 12,
    steps: [
      { kind: "wait", until: `${STORY_DATA} && ${has(STORY_REPLAY)}`, what: "the featured corner and its chart", timeoutS: 90 },
      { kind: "scroll", to: css('[data-step="0"]'), block: "story", s: 1.4 },
      { kind: "hold", s: 0.7 },
      { kind: "scroll", to: css('[data-step="1"]'), block: "story", s: 1 },
      { kind: "hold", s: 0.7 },
      { kind: "scroll", to: css('[data-step="2"]'), block: "story", s: 1 },
      { kind: "hold", s: 1 },
      { kind: "scroll", to: STORY_CHART, block: "start", s: 1.4 },
      { kind: "hold", s: 1 },
      // The replay plays once when most of it comes into view (charts/explain-corner.ts).
      { kind: "scroll", to: STORY_REPLAY, block: "story", s: 1 },
      { kind: "hold", s: 2.5 },
    ],
  },
  {
    id: "chat",
    caption: "Ask in plain words: Claude picks the analysis tools and answers with charts.",
    scriptedCaption: "Ask in plain words: the chat picks the analysis tools and answers with charts.",
    s: 20,
    steps: [
      {
        kind: "goto",
        path: "/chat",
        ready: `${has(css("#chat-question"))} && !document.querySelector("#chat-question").readOnly && ${has(button(QUESTION))}`,
        what: "the chat's question box",
      },
      { kind: "hold", s: 0.6 },
      { kind: "type", target: css("#chat-question"), text: QUESTION, s: 2 },
      { kind: "hold", s: 0.3 },
      { kind: "click", target: button("Send"), what: "Send" },
      // A real answer streams for up to 4 s on camera (the tool step, the first words), then cuts to
      // the finished answer; the scripted one is finished at once.
      { kind: "wait", until: ANSWER_DONE, what: "the answer and its chart", live: 4, timeoutS: 120 },
      { kind: "scroll", to: css(".re-chart--find-mistakes"), block: "start", s: 1.2 },
      { kind: "hold", s: 1 },
      { kind: "click", target: button("Show telemetry"), what: "Show telemetry" },
      { kind: "wait", until: has(chart("explain-corner")), what: "the corner's telemetry", live: 2, timeoutS: 90 },
      { kind: "hold", s: 0.6 },
      { kind: "scroll", to: css(".re-chart--explain-corner .replay-panel"), block: "story", s: 1.2 },
      { kind: "click", target: button("Play the replay"), what: "the replay's Play" },
      { kind: "hold", s: 3 },
    ],
  },
  {
    id: "mistakes",
    caption: "Explore any session since 2022, corner by corner.",
    s: 11,
    steps: [
      { kind: "goto", path: MISTAKES_PATH, ready: has(MISTAKES_ROW), what: "the session's flagged corners" },
      { kind: "hold", s: 1.2 },
      { kind: "scroll", to: css("#results-title"), block: "start", s: 1.2 },
      { kind: "hold", s: 0.6 },
      { kind: "click", target: MISTAKES_ROW, what: "a flagged corner's Show telemetry" },
      { kind: "wait", until: has(chart("explain-corner")), what: "the corner's chart", live: 2, timeoutS: 90 },
      { kind: "hold", s: 1 },
      { kind: "scroll", to: css(".re-chart--explain-corner .replay-panel"), block: "story", s: 1.2 },
      { kind: "hold", s: 2.6 },
    ],
  },
  {
    id: "styles",
    caption: "Compare how teammates drive the same car.",
    s: 11,
    steps: [
      { kind: "goto", path: STYLES_PATH, ready: has(chart("compare-styles")), what: "HAM and LEC's style chart" },
      { kind: "hold", s: 1 },
      { kind: "scroll", to: heading("HAM and LEC"), block: "start", s: 1.4 },
      { kind: "hold", s: 1.2 },
      { kind: "click", target: choice("Qualifying"), what: "the Qualifying filter" },
      { kind: "hold", s: 1.4 },
      { kind: "scroll", to: heading("Style map"), block: "start", s: 1.3 },
      { kind: "hold", s: 2.5 },
    ],
  },
  {
    id: "report",
    caption: "Checked against simple baselines, with intervals and limits stated.",
    s: 9,
    steps: [
      { kind: "goto", path: "/report", ready: has(DETECTORS), what: "the report's detector comparison" },
      { kind: "hold", s: 1.4 },
      { kind: "scroll", to: DETECTORS, block: "start", s: 1.8 },
      { kind: "hold", s: 1 },
      { kind: "click", target: choice("2026 test events"), what: "the 2026 test events switch" },
      { kind: "hold", s: 2.5 },
    ],
  },
  {
    id: "end",
    caption: null,
    s: 5,
    steps: [{ kind: "endcard" }, { kind: "hold", s: 5 }],
  },
];

// ---- The end card -------------------------------------------------------------------------------

export interface EndCard {
  name: string;
  /** The site's address as shown: the host, without the scheme. */
  address: string;
  connector: string;
  fanProject: string;
  credit: string | null;
}

/** "github.com/yunichun16" for "https://github.com/yunichun16". */
export function bareLink(href: string): string {
  return href.replace(/^https?:\/\//, "").replace(/^www\./, "").replace(/\/+$/, "");
}

/** The end card's words, from the site's own content (content/site.ts) and the site's address. */
export function endCard(siteUrl: string, label?: string): EndCard {
  const credit = site.credit;
  return {
    name: site.name,
    address: label ?? bareLink(siteUrl),
    connector: "Also inside Claude as a connector",
    fanProject: site.shortCaveat,
    credit: credit ? [`Built by ${credit.name}`, ...credit.links.map((link) => bareLink(link.href))].join("  ·  ") : null,
  };
}

/**
 * What a step takes on camera beyond its own seconds, at most: the round trips to the page and
 * the frame a smooth scroll ends on (about 20 ms measured), and a wait's polling (every 100 ms).
 */
export const SLACK_S = { step: 0.05, wait: 0.1 } as const;

/**
 * The most a step can take on camera, in seconds: a `live` wait counted in full, a click as the
 * pointer's glide and press, typing as a click into the box and the typing itself, each with its
 * slack. Off-camera steps take none; a hold and the end card's fade start take no slack.
 */
export function onCameraStep(step: Step): number {
  const click = POINTER.glideS + POINTER.pressS;
  switch (step.kind) {
    case "goto":
    case "endcard":
      return 0;
    case "wait":
      return step.live ? step.live + SLACK_S.wait : 0;
    case "hold":
      return step.s;
    case "scroll":
      return step.s + SLACK_S.step;
    case "click":
      return click + SLACK_S.step;
    case "type":
      return click + step.s + SLACK_S.step;
  }
}

/** The most a scene's steps can take on camera; it must fit the scene's length `s`. */
export function onCameraLength(scene: Scene): number {
  return Math.round(scene.steps.reduce((sum, step) => sum + onCameraStep(step), 0) * 1000) / 1000;
}

/** The total length the storyboard asks for, in seconds. */
export function plannedLength(scenes: readonly Scene[] = STORYBOARD, skip: ReadonlySet<string> = new Set()): number {
  return scenes.filter((scene) => !skip.has(scene.id)).reduce((sum, scene) => sum + scene.s, 0);
}
