// The chat's words on screen: the answer text as blocks, and the short labels around it.
//
// The system prompt asks for "Plain text with short paragraphs and bullets. No tables or
// headings." (engine/src/race_engineer/api/chat/prompt.py), so toBlocks handles only that:
// paragraphs split on blank lines, "- ", "• " and "* " bullets, "1. " numbered lists, and
// **bold** and `code` inside a line. Everything else is literal text. The blocks are data that
// the view renders as React text, so "<script>" in an answer stays the eight characters it is.

export type Inline =
  | { kind: "text"; text: string }
  | { kind: "bold"; text: string }
  | { kind: "code"; text: string };

/** One line of inline pieces. */
export type InlineLine = Inline[];

export type Block =
  | { kind: "paragraph"; lines: InlineLine[] } // lines keep their single line breaks
  | { kind: "bullets"; items: InlineLine[] }
  | { kind: "numbered"; start: number; items: InlineLine[] };

const BULLET = /^\s*[-•*]\s+(.*)$/;
const NUMBERED = /^\s*(\d{1,3})\.\s+(.*)$/;
const CODE = /`([^`\n]+)`/g;
// Bold needs text that doesn't start or end with a space, so "2 ** 3 ** 4" stays as it is.
const BOLD = /\*\*(?!\s)(.+?)(?<!\s)\*\*/g;

function push(out: Inline[], piece: Inline): void {
  if (piece.text === "") return;
  const last = out[out.length - 1];
  if (piece.kind === "text" && last?.kind === "text") {
    out[out.length - 1] = { kind: "text", text: last.text + piece.text };
  } else {
    out.push(piece);
  }
}

function bold(text: string, out: Inline[]): void {
  let at = 0;
  for (const match of text.matchAll(BOLD)) {
    const start = match.index ?? 0;
    push(out, { kind: "text", text: text.slice(at, start) });
    push(out, { kind: "bold", text: match[1] });
    at = start + match[0].length;
  }
  push(out, { kind: "text", text: text.slice(at) });
}

/** `code` first (nothing inside it is formatted), then **bold** in the text between, so bold
 *  can't wrap code. An unclosed marker stays literal, which is also how a half-streamed "**bo"
 *  shows until its end arrives. */
export function inline(text: string): InlineLine {
  const out: Inline[] = [];
  let at = 0;
  for (const match of text.matchAll(CODE)) {
    const start = match.index ?? 0;
    bold(text.slice(at, start), out);
    push(out, { kind: "code", text: match[1] });
    at = start + match[0].length;
  }
  bold(text.slice(at), out);
  return out;
}

type Draft =
  | { kind: "paragraph"; lines: string[] }
  | { kind: "bullets"; items: string[] }
  | { kind: "numbered"; start: number; items: string[] };

export function toBlocks(text: string): Block[] {
  const drafts: Draft[] = [];
  let current: Draft | null = null;
  for (const raw of text.replace(/\r\n?/g, "\n").split("\n")) {
    const line = raw.trimEnd();
    if (line.trim() === "") {
      current = null;
      continue;
    }
    const bullet = BULLET.exec(line);
    if (bullet) {
      if (current?.kind !== "bullets") {
        current = { kind: "bullets", items: [] };
        drafts.push(current);
      }
      current.items.push(bullet[1]);
      continue;
    }
    const numbered = NUMBERED.exec(line);
    if (numbered) {
      if (current?.kind !== "numbered") {
        current = { kind: "numbered", start: Number(numbered[1]), items: [] };
        drafts.push(current);
      }
      current.items.push(numbered[2]);
      continue;
    }
    // An indented line right after a list item continues that item.
    if (current !== null && current.kind !== "paragraph" && /^\s/.test(line)) {
      const items = current.items;
      items[items.length - 1] += " " + line.trim();
      continue;
    }
    if (current?.kind !== "paragraph") {
      current = { kind: "paragraph", lines: [] };
      drafts.push(current);
    }
    current.lines.push(line.trim());
  }
  return drafts.map((draft): Block => {
    switch (draft.kind) {
      case "paragraph":
        return { kind: "paragraph", lines: draft.lines.map(inline) };
      case "bullets":
        return { kind: "bullets", items: draft.items.map(inline) };
      case "numbered":
        return { kind: "numbered", start: draft.start, items: draft.items.map(inline) };
    }
  });
}

// ---- Labels around the answer ----

const TOOL_VERBS: Record<string, string> = {
  find_session: "Finding the session",
  list_sessions: "Listing the sessions",
  get_race_summary: "Summarising the race",
  find_mistakes: "Finding mistakes",
  explain_corner: "Explaining the corner",
  compare_laps: "Comparing laps",
  compare_driving_styles: "Comparing driving styles",
};

const MAX_VALUE = 40;

function shown(key: string, value: unknown): string | null {
  if (typeof value !== "string" && typeof value !== "number") return null;
  let text = String(value).trim();
  if (text === "" || key === "limit") return null;
  if (text.length > MAX_VALUE) text = text.slice(0, MAX_VALUE - 1).trimEnd() + "…";
  if (key === "lap" || key === "lap_a" || key === "lap_b") return `lap ${text}`;
  if (key === "corner") return `turn ${text}`;
  if (key === "round") return `round ${text}`;
  // find_session counts back from the newest session: 0 is the latest.
  if (key === "recent") return text === "0" ? "latest" : text === "1" ? "previous" : `${text} back`;
  return text;
}

/**
 * A tool step's chip: what the tool does, then the arguments the model gave, in its order, e.g.
 * "Finding mistakes · monza · LEC · 2025 · Q". Only strings and numbers show; `limit` is left
 * out, laps, corners and rounds get a word in front, and find_session's `recent` reads as
 * "latest", "previous" or "2 back".
 */
export function toolStepLabel(name: string, input: Record<string, unknown>): string {
  const verb =
    TOOL_VERBS[name] ?? (name.charAt(0).toUpperCase() + name.slice(1)).replaceAll("_", " ");
  const values = Object.entries(input)
    .map(([key, value]) => shown(key, value))
    .filter((value): value is string => value !== null);
  return [verb, ...values].join(" · ");
}

// ---- Chart card headings ----

export interface ChartHeading {
  /** The card's title: what the chart shows ("Flagged mistakes · LEC"). */
  title: string;
  /** Which session or season ("2026 Azerbaijan GP · Race"), or null when the payload lacks it. */
  subtitle: string | null;
  /** The chart's accessible name, in full words ("Flagged mistakes, 2026 Azerbaijan Grand Prix,
   *  Race"). */
  label: string;
}

function field(data: Record<string, unknown>, key: string): string | null {
  const value = data[key];
  if (typeof value === "number" && Number.isFinite(value)) return String(value);
  if (typeof value !== "string") return null;
  const text = value.trim();
  return text === "" ? null : text.slice(0, MAX_VALUE * 2);
}

/** "Azerbaijan Grand Prix" as "Azerbaijan GP", for titles with little room. */
export function shortEvent(event: string): string {
  return event.replace(/\bGrand Prix\b/, "GP");
}

const GENERIC_TITLES: Record<string, string> = {
  "find-mistakes": "Flagged mistakes",
  "explain-corner": "Corner telemetry",
  "race-summary": "Race summary",
  "compare-styles": "Driving styles",
  "compare-laps": "Lap comparison",
};

/**
 * A chart card's heading, from the chart's payload (never the model's text). Fields the payload
 * lacks are left out, so an odd payload still gets a sensible title.
 */
export function chartHeading(bundle: string, data: unknown): ChartHeading {
  const d: Record<string, unknown> =
    typeof data === "object" && data !== null && !Array.isArray(data) ? (data as Record<string, unknown>) : {};
  const generic = Object.hasOwn(GENERIC_TITLES, bundle) ? GENERIC_TITLES[bundle] : "Chart";
  const year = field(d, "year");
  const event = field(d, "event");
  const session = field(d, "session");
  const where = (short: boolean): string | null => {
    const name = event === null ? null : short ? shortEvent(event) : event;
    const head = [year, name].filter((part) => part !== null).join(" ");
    const parts = [head, session].filter((part): part is string => part !== null && part !== "");
    return parts.length > 0 ? parts.join(short ? " · " : ", ") : null;
  };
  const pair = (a: string | null, b: string | null): string | null => (a && b ? `${a} and ${b}` : null);

  let title = generic;
  let subtitle = where(true);
  let label = where(false);
  switch (bundle) {
    case "find-mistakes": {
      const driver = field(d, "driver");
      if (driver) title = `${generic} · ${driver}`;
      break;
    }
    case "explain-corner": {
      const driver = field(d, "driver");
      const lap = field(d, "lap_number");
      const turn = field(d, "turn");
      if (driver && lap && turn) title = `${driver} · lap ${lap} · turn ${turn.toUpperCase()}`;
      break;
    }
    case "race-summary":
      if (session === "Sprint") title = "Sprint summary";
      break;
    case "compare-styles": {
      const both = pair(field(d, "driver_a"), field(d, "driver_b"));
      if (both) title = `${generic}: ${both}`;
      subtitle = year ? `${year} season` : null;
      label = subtitle;
      break;
    }
    case "compare-laps": {
      const drivers = Array.isArray(d.drivers) ? d.drivers : [];
      const code = (item: unknown): string | null =>
        typeof item === "object" && item !== null ? field(item as Record<string, unknown>, "code") : null;
      const both = pair(code(drivers[0]), code(drivers[1]));
      if (both) title = `${generic}: ${both}`;
      break;
    }
  }
  return { title, subtitle, label: label ? `${title}, ${label}` : title };
}

/** The question meter under an answer. */
export function questionMeter(left: number): string {
  if (left <= 0) return "No questions left in this conversation";
  if (left === 1) return "Last question in this conversation";
  return `${left} questions left in this conversation`;
}

/** The development-only line under an answer: "{model} · {s} s · ${cost}", "simulated" for
 *  the scripted model (fake mode prices its made-up usage). */
export function devMeta(meta: { model: string; ms: number; cost_usd: number | null }): string {
  const parts = [meta.model || "unknown model", `${(meta.ms / 1000).toFixed(1)} s`];
  if (meta.cost_usd !== null) {
    parts.push(`$${meta.cost_usd.toFixed(4)}${meta.model === "scripted" ? " simulated" : ""}`);
  }
  return parts.join(" · ");
}
