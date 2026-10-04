/**
 * The example questions the site offers: the landing page's three chips (`landing: true`) and
 * the chat's question chips (plan 7.2, spec h.1). The chat shows them all in their groups from
 * 640 px, and the four marked `phone` on a phone.
 *
 * The ids are stable: each question's `saved` is its own id, the name of its saved example
 * answer (data/saved/{id}.json, recorded by scripts/saved.ts; plan 4.1), so reword a question
 * freely but never reuse or rename an id. A recording whose question no longer matches is
 * flagged by `saved.ts check`. `chip` is a short label for the landing's chips; a chip still
 * sends the full `text`, and the full text is its tooltip.
 *
 * Leclerc at Monza 2025 is left out on purpose: none of his 66 scored corners there was flagged,
 * so a follow-up would have nothing to explain.
 */

export type QuestionGroup = "races" | "mistakes" | "head-to-head" | "style" | "out-of-scope";

export interface ExampleQuestion {
  id: string;
  /** The question as it is sent. */
  text: string;
  /** A short label for a chip with little room (the landing's); the chip still sends `text`. */
  chip?: string;
  group: QuestionGroup;
  /** One of the landing page's three chips. */
  landing?: boolean;
  /** One of the four chips the chat's empty page shows on a phone (all show from 640 px). */
  phone?: boolean;
  /** A word on why it's there, shown beside the chip: "to see it decline". */
  hint?: string;
  /** The id of its saved answer, shown when the chat is rate-limited, capped, paused or off. */
  saved?: string;
}

export const QUESTIONS: readonly ExampleQuestion[] = [
  { id: "latest", text: "What's the most recent race you can analyse?", group: "races", saved: "latest" },
  {
    id: "monaco-2023",
    text: "Summarise the 2023 Monaco Grand Prix.",
    chip: "Summarise the 2023 Monaco Grand Prix",
    group: "races",
    landing: true,
    phone: true,
    saved: "monaco-2023",
  },
  {
    id: "last-race-mistakes",
    text: "Who made the biggest mistakes in the last race?",
    group: "mistakes",
    phone: true,
    saved: "last-race-mistakes",
  },
  {
    id: "abu-dhabi-gain",
    text: "Where did Norris gain on Piastri in 2025 Abu Dhabi qualifying?",
    chip: "Norris vs Piastri, Abu Dhabi",
    group: "head-to-head",
    landing: true,
    phone: true,
    saved: "abu-dhabi-gain",
  },
  {
    id: "ham-lec-style",
    text: "How does Hamilton's driving style differ from Leclerc's?",
    chip: "Hamilton vs Leclerc style",
    group: "style",
    landing: true,
    phone: true,
    saved: "ham-lec-style",
  },
  { id: "baku-sc", text: "Were there any safety cars in the Baku race?", group: "races", saved: "baku-sc" },
  {
    id: "out-of-scope",
    text: "Who will win the 2027 championship?",
    group: "out-of-scope",
    hint: "to see it decline",
    saved: "out-of-scope",
  },
];

/** The chat's chip groups, in the order its empty page lists them, with their labels. */
export const QUESTION_GROUPS: readonly { id: QuestionGroup; label: string }[] = [
  { id: "races", label: "Races" },
  { id: "mistakes", label: "Mistakes" },
  { id: "head-to-head", label: "Head to head" },
  { id: "style", label: "Style" },
  { id: "out-of-scope", label: "Out of scope" },
];

/** The landing page's chips, in order. */
export const LANDING_QUESTIONS: readonly ExampleQuestion[] = QUESTIONS.filter((q) => q.landing);

/** What a chip shows: its short label, else the question itself. */
export function chipLabel(q: ExampleQuestion): string {
  return q.chip ?? q.text;
}
