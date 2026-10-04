"use client";

import Link from "next/link";
import {
  useEffect,
  useMemo,
  useRef,
  useState,
  useSyncExternalStore,
  type FocusEvent,
  type PointerEvent,
  type RefObject,
} from "react";
import { LANDING_CORNER } from "@/content/featured";
import {
  entryCaption,
  HALO_R,
  heroCaption,
  heroEntries,
  heroGeometry,
  heroServerSnapshot,
  heroSnapshot,
  linkLabel,
  pickAnnouncement,
  pickLabel,
  readoutLabel,
  ROAD_WIDTH,
  shortType,
  startHero,
  subscribeHero,
  VIEW,
  type HeroExample,
} from "./hero-examples";
import { useStartFeaturedWhenIdle } from "./useFeaturedCorner";

/** How long each example stays, counted from its arrival (the approved mockup: 4.5 s). */
const DWELL_MS = 4500;
/** The first example stays a little longer: its line draws while the page settles. */
const FIRST_EXTRA_MS = 600;

const { driver: DRIVER, year: YEAR, lap: LAP, turn: TURN = "" } = LANDING_CORNER.args;
/** The readout's label before the file is here: what the landing example already says. */
const EARLY_LABEL = `${DRIVER} · ${YEAR} · Lap ${LAP ?? ""}`;
/** The captions the card reserves room for before the file is here, so nothing moves when it is. */
const RESERVED = heroEntries().map(entryCaption);

/** A wrapped line never starts with a separator: each " · " sticks to the word before it. */
const keepDots = (text: string) => text.replaceAll(" · ", "\u00a0· ");

/**
 * The hero's drawing (plan 13, 2026-10-04, as the approved mockup has it): real flagged corners,
 * each on its real circuit with only that corner lit, rotating every 4.5 s with a crossfade. It
 * starts on the landing example (the story below tells that corner), reads one static file and
 * never calls the API.
 *
 * Rotation pauses while the pointer is over the card, while something in it has keyboard focus,
 * while the tab is hidden and while the card is off screen; the pause button and the dots work
 * at any time, and with reduced motion nothing turns or draws by itself. Each example is a link
 * to its corner in the mistake explorer. All the examples are stacked in one grid cell, and each
 * reserves every caption's room, so the card never changes size between them.
 *
 * Until the file is read the card shows the first example's frame with no drawing; without the
 * file (a checkout with no snapshots) it shows the abstract circuit as a last resort.
 * It also starts the landing example's load once the page is idle (spec h.2).
 */
export function HeroTrack() {
  useStartFeaturedWhenIdle();
  const hero = useSyncExternalStore(subscribeHero, heroSnapshot, heroServerSnapshot);
  useEffect(() => startHero(), []);
  const examples = hero.status === "ready" ? hero.examples : null;
  const ref = useRef<HTMLDivElement>(null);
  const rotation = useRotation(examples, ref);
  const captions = useMemo(() => (examples ? [...new Set([...examples.map(heroCaption), ...RESERVED])] : RESERVED), [examples]);

  return (
    <div
      ref={ref}
      // The pointer and focus are followed from the start, so a pointer already resting on the
      // card when the examples arrive holds the first one too.
      {...rotation.handlers}
      {...(examples ? CAROUSEL : {})}
      // Stacked (under 960 px) the card stops at 600 px, so the drawing doesn't grow to fill a tablet.
      className="@container glass relative w-full max-w-[600px] rounded-panel px-5 pt-4.5 pb-3.5 sm:px-6 sm:pt-5 min-[960px]:max-w-none"
    >
      {examples ? (
        <>
          <div className="grid">
            {examples.map((example, i) => (
              <Slide
                key={example.id}
                example={example}
                position={i}
                count={examples.length}
                captions={captions}
                active={i === rotation.index}
                animate={!rotation.reduced}
              />
            ))}
          </div>
          {examples.length > 1 ? (
            <Controls examples={examples} index={rotation.index} paused={rotation.paused} onPick={rotation.pick} onToggle={rotation.toggle} />
          ) : (
            <ControlsRoom />
          )}
          <p className="sr-only" aria-live="polite" aria-atomic="true">
            {rotation.message}
          </p>
        </>
      ) : hero.status === "missing" ? (
        <AbstractTrack />
      ) : (
        <Frame />
      )}
    </div>
  );
}

// --- Rotation -------------------------------------------------------------------------------

const CAROUSEL = { role: "region", "aria-roledescription": "carousel", "aria-label": "Flagged corners from real sessions" } as const;

interface Rotation {
  index: number;
  paused: boolean;
  reduced: boolean;
  message: string;
  pick: (i: number) => void;
  toggle: () => void;
  handlers: {
    onPointerEnter: (e: PointerEvent) => void;
    onPointerLeave: (e: PointerEvent) => void;
    onFocus: (e: FocusEvent) => void;
    onBlur: (e: FocusEvent) => void;
  };
}

function useRotation(examples: HeroExample[] | null, ref: RefObject<HTMLDivElement | null>): Rotation {
  const n = examples?.length ?? 0;
  const reduced = useReducedMotion();
  const hidden = useTabHidden();
  const onScreen = useOnScreen(ref);
  const [index, setIndex] = useState(0);
  // null until the reader presses the button: then rotation follows the motion setting.
  const [userPaused, setUserPaused] = useState<boolean | null>(null);
  const [hovering, setHovering] = useState(false);
  const [focused, setFocused] = useState(false);
  const [started, setStarted] = useState(false);
  const [message, setMessage] = useState("");

  const paused = userPaused ?? reduced;
  const running = n > 1 && started && !paused && !hovering && !focused && !hidden && onScreen;

  // The first example gets a little longer before the clock starts.
  useEffect(() => {
    if (!n) return;
    const timer = window.setTimeout(() => setStarted(true), reduced ? 0 : FIRST_EXTRA_MS);
    return () => window.clearTimeout(timer);
  }, [n, reduced]);

  useEffect(() => {
    if (!running) return;
    const timer = window.setTimeout(() => {
      setIndex((i) => (i + 1) % n);
      setMessage(""); // turning by itself is not announced
    }, DWELL_MS);
    return () => window.clearTimeout(timer);
  }, [running, index, n]);

  return {
    index,
    paused,
    reduced,
    message,
    pick(i) {
      if (!examples || i === index) return;
      setIndex(i);
      setMessage(pickAnnouncement(examples[i]));
    },
    toggle() {
      if (paused) {
        // Play means play now, even with the pointer or focus still on the card.
        setUserPaused(false);
        setHovering(false);
        setFocused(false);
      } else setUserPaused(true);
    },
    handlers: {
      onPointerEnter: (e) => {
        if (e.pointerType !== "touch") setHovering(true);
      },
      onPointerLeave: () => setHovering(false),
      // Keyboard focus pauses; a mouse click on a control doesn't (the pointer already does).
      onFocus: (e) => {
        if (e.target instanceof Element && e.target.matches(":focus-visible")) setFocused(true);
      },
      onBlur: (e) => {
        if (!e.currentTarget.contains(e.relatedTarget as Node | null)) setFocused(false);
      },
    },
  };
}

const REDUCED = "(prefers-reduced-motion: reduce)";

function useReducedMotion(): boolean {
  return useSyncExternalStore(
    (onChange) => {
      const query = window.matchMedia(REDUCED);
      query.addEventListener("change", onChange);
      return () => query.removeEventListener("change", onChange);
    },
    () => window.matchMedia(REDUCED).matches,
    () => false,
  );
}

function useTabHidden(): boolean {
  return useSyncExternalStore(
    (onChange) => {
      document.addEventListener("visibilitychange", onChange);
      return () => document.removeEventListener("visibilitychange", onChange);
    },
    () => document.visibilityState === "hidden",
    () => false,
  );
}

function useOnScreen(ref: RefObject<Element | null>): boolean {
  const [onScreen, setOnScreen] = useState(true);
  useEffect(() => {
    const node = ref.current;
    if (!node || typeof IntersectionObserver === "undefined") return;
    const observer = new IntersectionObserver(([entry]) => setOnScreen(entry.isIntersecting));
    observer.observe(node);
    return () => observer.disconnect();
  }, [ref]);
  return onScreen;
}

// --- One example ----------------------------------------------------------------------------

// The approved mockup's timing: the line draws over 1.1 s, the corner lights at about 0.9 s.
const DRAW_CLASS =
  "[stroke-dasharray:1] [stroke-dashoffset:1] animate-[draw_1.1s_var(--ease-out)_.1s_forwards] motion-reduce:animate-none motion-reduce:[stroke-dashoffset:0]";
const LIGHT_CLASS = "opacity-0 animate-[fade-in_.4s_var(--ease-out)_.9s_forwards] motion-reduce:animate-none motion-reduce:opacity-100";

function Slide({
  example,
  position,
  count,
  captions,
  active,
  animate,
}: {
  example: HeroExample;
  position: number;
  count: number;
  captions: string[];
  active: boolean;
  animate: boolean;
}) {
  const g = useMemo(() => heroGeometry(example), [example]);
  const moving = active && animate;
  return (
    <div
      role="group"
      aria-roledescription="slide"
      aria-label={`${position + 1} of ${count}`}
      inert={!active}
      className={
        "col-start-1 row-start-1 flex flex-col transition-[opacity,visibility] duration-450 ease-(--ease-out) " +
        (active ? "visible opacity-100" : "pointer-events-none invisible opacity-0")
      }
    >
      <Link href={example.href} prefetch={false} aria-label={linkLabel(example)} className="group flex flex-1 flex-col rounded-2xl">
        <svg viewBox={`0 0 ${VIEW.w} ${VIEW.h}`} aria-hidden="true" focusable="false" className="block h-auto w-full">
          <path d={g.path} fill="none" stroke="var(--track)" strokeWidth={ROAD_WIDTH} strokeLinejoin="round" strokeLinecap="round" />
          {g.start && (
            <line {...g.start} stroke="var(--trace)" strokeOpacity={0.55} strokeWidth={2} strokeLinecap="round" />
          )}
          {/* The draw class is there only while the example is shown, so it draws in each time. */}
          <path
            d={g.path}
            pathLength={1}
            className={moving ? DRAW_CLASS : undefined}
            fill="none"
            stroke="var(--trace)"
            strokeWidth={2.5}
            strokeLinejoin="round"
            strokeLinecap="round"
          />
          <g className={moving ? LIGHT_CLASS : undefined}>
            <circle cx={g.corner[0]} cy={g.corner[1]} r={HALO_R} fill="var(--color-text-danger)" opacity={0.14} />
            <circle cx={g.corner[0]} cy={g.corner[1]} r={7} fill="var(--color-text-danger)" />
            <text
              x={g.label.x}
              y={g.label.y}
              textAnchor="middle"
              dominantBaseline="central"
              fill="var(--color-text-danger)"
              // LABEL_FONT units on a narrow card (about 13 px on a phone), 14 on a wider one.
              className="font-mono text-[18px] font-medium tracking-[0.04em] @min-[440px]:text-[14px]"
            >
              {g.label.text}
            </text>
          </g>
        </svg>
        <Readout label={readoutLabel(example)} turn={example.turn} type={shortType(example.type_text)} lost={example.time_lost_s} />
        <Caption texts={captions} shown={heroCaption(example)} className="decoration-current underline-offset-4 group-hover:underline" />
      </Link>
    </div>
  );
}

/**
 * The readout, a glass-inset row under the drawing at every width (the approved mockup): the
 * label and "Turn 5 · over-slowing" on the left, the time lost on the right. On a narrow card the
 * label takes the whole first line and the corner's type goes under its number. It grows to fill
 * the slide, so every example's readout is the same size. Without a number (`lost` null) the
 * number's room is kept unseen.
 */
function Readout({ label, turn, type, lost }: { label: string; turn: string; type: string; lost: number | null }) {
  return (
    <div className="glass-inset mt-2 grid flex-1 grid-cols-[minmax(0,1fr)_auto] content-center items-center gap-x-3 gap-y-1 rounded-2xl px-4 py-3">
      <p className="micro col-span-2 @min-[440px]:col-span-1">{keepDots(label)}</p>
      <p className="col-start-1 text-title text-fg">
        Turn {turn}
        <span className="block font-normal text-muted @min-[440px]:inline">
          <span className="hidden @min-[440px]:inline">{" · "}</span>
          {type}
        </span>
      </p>
      <p
        aria-hidden={lost === null || undefined}
        className={
          "col-start-2 row-start-2 text-result-l whitespace-nowrap text-danger @min-[440px]:row-span-2 @min-[440px]:row-start-1 " +
          (lost === null ? "invisible" : "")
        }
      >
        +{(lost ?? 0).toFixed(2)}&nbsp;s
      </p>
    </div>
  );
}

/**
 * The caption line under the readout: the shown text, with every other caption laid under it
 * unseen, so the line is as tall as the longest caption on every example and while loading.
 */
function Caption({ texts, shown, className }: { texts: string[]; shown: string; className?: string }) {
  return (
    <p className={`micro mt-1.5 grid ${className ?? ""}`}>
      <span className="col-start-1 row-start-1">{keepDots(shown)}</span>
      {texts.map((text) => (
        <span key={text} aria-hidden="true" className="invisible col-start-1 row-start-1">
          {keepDots(text)}
        </span>
      ))}
    </p>
  );
}

// --- The controls ---------------------------------------------------------------------------

function Controls({
  examples,
  index,
  paused,
  onPick,
  onToggle,
}: {
  examples: HeroExample[];
  index: number;
  paused: boolean;
  onPick: (i: number) => void;
  onToggle: () => void;
}) {
  return (
    <div className="-mr-2 mt-0.5 flex items-center justify-end">
      <div role="group" aria-label="Examples" className="flex">
        {examples.map((example, i) => (
          <button
            key={example.id}
            type="button"
            aria-label={pickLabel(example)}
            aria-current={i === index ? "true" : undefined}
            onClick={() => onPick(i)}
            className="group/dot grid size-11 cursor-pointer place-items-center rounded-full"
          >
            <span
              aria-hidden="true"
              className={
                "block h-2 rounded-full transition-[width,background-color] duration-(--dur-state) ease-(--ease-out) " +
                (i === index ? "w-5 bg-fg" : "w-2 bg-muted group-hover/dot:bg-fg")
              }
            />
          </button>
        ))}
      </div>
      <button
        type="button"
        onClick={onToggle}
        aria-label={paused ? "Play the examples" : "Pause the examples"}
        className="grid size-11 shrink-0 cursor-pointer place-items-center rounded-xl text-muted transition-colors duration-(--dur-hover) hover:bg-hairline hover:text-fg forced-colors:text-[ButtonText]"
      >
        {paused ? <PlayIcon /> : <PauseIcon />}
      </button>
    </div>
  );
}

/** The controls' room, kept while there is nothing to control. */
function ControlsRoom() {
  return <div aria-hidden="true" className="mt-0.5 h-11" />;
}

function PauseIcon() {
  return (
    <svg viewBox="0 0 24 24" aria-hidden="true" focusable="false" className="size-4.5" fill="currentColor">
      <rect x="6" y="5" width="4" height="14" rx="1.2" />
      <rect x="14" y="5" width="4" height="14" rx="1.2" />
    </svg>
  );
}

function PlayIcon() {
  return (
    <svg viewBox="0 0 24 24" aria-hidden="true" focusable="false" className="size-4.5" fill="currentColor">
      <path d="M8 5.6v12.8a1 1 0 0 0 1.5.86l10.2-6.4a1 1 0 0 0 0-1.72L9.5 4.74A1 1 0 0 0 8 5.6Z" />
    </svg>
  );
}

// --- Before the file, and without it ---------------------------------------------------------

/** The first example's frame while the file loads: no drawing yet, just a quiet line. */
function Frame() {
  return (
    <>
      <div className="flex flex-col">
        <svg viewBox={`0 0 ${VIEW.w} ${VIEW.h}`} aria-hidden="true" focusable="false" className="block h-auto w-full">
          <line x1={120} y1={VIEW.h / 2} x2={280} y2={VIEW.h / 2} stroke="var(--track)" strokeWidth={ROAD_WIDTH} strokeLinecap="round" />
        </svg>
        <span className="sr-only">Loading real examples</span>
        <Readout label={EARLY_LABEL} turn={TURN} type={LANDING_CORNER.typeLabel ?? "flagged"} lost={null} />
        <Caption texts={RESERVED} shown={RESERVED[0]} />
      </div>
      <ControlsRoom />
    </>
  );
}

// An invented closed circuit, one Bezier loop drawn twice (the road, then the racing line): the
// last resort when the real examples aren't there. Only the flagged corner is lit.
const CIRCUIT = "M70 165 C60 85 140 45 230 57 C320 69 350 125 320 180 C295 225 220 220 185 195 C150 170 110 230 70 165 Z";
const CORNER = { x: 185, y: 195 };

function AbstractTrack() {
  return (
    <>
      <div className="flex flex-col">
        <svg
          viewBox={`0 0 ${VIEW.w} ${VIEW.h}`}
          role="img"
          aria-label={`An abstract circuit drawn as one line, with turn ${TURN} lit as the flagged corner`}
          className="block h-auto w-full"
        >
          <path d={CIRCUIT} fill="none" stroke="var(--track)" strokeWidth={18} strokeLinejoin="round" />
          <path d={CIRCUIT} pathLength={1} className="draw" fill="none" stroke="var(--trace)" strokeWidth={2.5} strokeLinecap="round" />
          <g className="lit">
            <circle cx={CORNER.x} cy={CORNER.y} r={HALO_R} fill="var(--color-text-danger)" opacity={0.14} />
            <circle cx={CORNER.x} cy={CORNER.y} r={7} fill="var(--color-text-danger)" />
            <text
              x={CORNER.x}
              y={CORNER.y + 42}
              textAnchor="middle"
              fill="var(--color-text-danger)"
              className="font-mono text-xs font-medium tracking-[0.08em]"
            >
              {`T${TURN}`}
            </text>
          </g>
        </svg>
        <Readout label={EARLY_LABEL} turn={TURN} type="flagged" lost={null} />
        <Caption texts={RESERVED} shown="Abstract circuit · the flagged corner lit" />
      </div>
      <ControlsRoom />
    </>
  );
}
