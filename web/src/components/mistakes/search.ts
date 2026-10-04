/**
 * The /mistakes search box, read into find_session's fields (plan 7.3's SessionSearch, with
 * find_session taking fields rather than free text: engine tools/find_session.py).
 *
 * The box takes what people type ("monza quali 2025", "last race", "Baku sprint 2026") and splits
 * it into the fields the tool reads: a four-digit season, a session word, "last", "latest" or
 * "most recent" as a count back from the newest session (recent=0), "round 5" as a round, and
 * whatever is left as the event's name. The tool does the rest (aliases, misspellings,
 * candidates), so this only has to keep those words out of the event text, where the tool would
 * read them as a place that doesn't exist.
 */

/** The session codes the tool's session field reads ("SQ" also finds a 2023 Sprint Shootout). */
export type SearchSession = "Q" | "R" | "S" | "SQ";

/** find_session's fields, as the REST route takes them. */
export interface SessionSearchFields {
  event?: string;
  year?: number;
  session?: SearchSession;
  round?: number;
  recent?: number;
}

/** The longest event text the tool takes. */
export const MAX_SEARCH_LENGTH = 100;

// Session phrases, longest first so "sprint qualifying" wins over "sprint". The tool's own
// aliases (tools/sessions.py SESSION_ALIASES), less "gp" and "grand prix", which are part of
// every event's name, and the one-letter "s", which also starts "S Paulo" and "S Arabia".
const SESSION_PHRASES: readonly (readonly [string, SearchSession])[] = [
  ["sprint qualifying", "SQ"],
  ["sprint shootout", "SQ"],
  ["sprint quali", "SQ"],
  ["sprint qualy", "SQ"],
  ["sprint race", "S"],
  ["qualifying", "Q"],
  ["shootout", "SQ"],
  ["qualies", "Q"],
  ["qually", "Q"],
  ["qualis", "Q"],
  ["sprint", "S"],
  ["quali", "Q"],
  ["qualy", "Q"],
  ["quals", "Q"],
  ["race", "R"],
  ["sq", "SQ"],
  ["ss", "SQ"],
  ["q", "Q"],
  ["r", "R"],
];

// "last race", "the latest quali", "most recent": the newest session (of that kind).
const RECENT_PHRASES = ["most recent", "latest", "newest", "last"];

// Small words around the names that no event or place name has as a word of its own: "the last
// race", "Monza in 2025", "the race at Baku".
const FILLER = new Set(["the", "a", "at", "in", "of", "for", "from"]);

const YEAR = /^(?:19|20)\d{2}$/;
const ROUND_NUMBER = /^\d{1,2}$/;

/** The words of the box: split on spaces and commas, end punctuation dropped. */
function words(text: string): string[] {
  return text
    .split(/[\s,;]+/)
    .map((w) => w.replace(/^[("'“‘]+|[)"'”’.!?:]+$/g, ""))
    .filter(Boolean);
}

/** Removes the first run of `phrase`'s words from `list` (case aside); whether it was there. */
function takePhrase(list: string[], phrase: string): boolean {
  const parts = phrase.split(" ");
  for (let i = 0; i + parts.length <= list.length; i++) {
    if (parts.every((p, j) => list[i + j].toLowerCase() === p)) {
      list.splice(i, parts.length);
      return true;
    }
  }
  return false;
}

/**
 * The box's text as find_session's fields, or null when it names nothing to look for (empty,
 * or only filler). A season or a session alone asks for the newest such session (recent=0):
 * "2024" is the last session of 2024, "quali" the latest qualifying.
 */
export function parseSessionSearch(text: string): SessionSearchFields | null {
  const list = words(text.slice(0, 400));
  const fields: SessionSearchFields = {};

  const yearAt = list.findIndex((w) => YEAR.test(w));
  if (yearAt !== -1) {
    fields.year = Number(list[yearAt]);
    list.splice(yearAt, 1);
  }

  const roundAt = list.findIndex((w, i) => w.toLowerCase() === "round" && ROUND_NUMBER.test(list[i + 1] ?? ""));
  if (roundAt !== -1) {
    const round = Number(list[roundAt + 1]);
    list.splice(roundAt, 2);
    if (round >= 1) fields.round = round;
  }

  for (const [phrase, code] of SESSION_PHRASES) {
    if (takePhrase(list, phrase)) {
      fields.session = code;
      break;
    }
  }

  let recent = false;
  for (const phrase of RECENT_PHRASES) {
    if (takePhrase(list, phrase)) {
      recent = true;
      break;
    }
  }

  const event = list
    .filter((w) => !FILLER.has(w.toLowerCase()))
    .join(" ")
    .slice(0, MAX_SEARCH_LENGTH)
    .trim();
  if (event) fields.event = event;

  // A round picks its weekend by itself; the tool refuses round and recent together.
  if (fields.round === undefined && (recent || (!event && (fields.year !== undefined || fields.session)))) {
    fields.recent = 0;
  }

  return fields.event || fields.round !== undefined || fields.recent !== undefined ? fields : null;
}
