/**
 * The landing's hand-over to the chat (plan decision 8): a question from the Ask box or a chip is
 * left in this tab's sessionStorage under "re.chat.pending", and the page goes to /chat, which
 * reads it once, deletes it and sends it. Nothing is sent without that click, and a link can
 * never send a question: if storage is blocked, the page goes to /chat?q=…, which only prefills
 * the composer.
 */

import { withQuery } from "../../lib/url.ts";

export const PENDING_KEY = "re.chat.pending";

/** The chat's limit on one question, in characters. */
export const MAX_QUESTION_CHARS = 2000;

/** The part of the Storage interface used here; sessionStorage satisfies it. */
export interface PendingStorage {
  setItem(key: string, value: string): void;
}

/**
 * Leaves `question` for the chat and returns where to go: "/chat" when it was saved, else
 * "/chat?q=…" (a prefill). Null for a blank question, which goes nowhere.
 */
export function handOff(storage: PendingStorage | null, question: string): string | null {
  const text = question.trim().slice(0, MAX_QUESTION_CHARS);
  if (!text) return null;
  if (storage) {
    try {
      storage.setItem(PENDING_KEY, text);
      return "/chat";
    } catch {
      // full or blocked: fall through to the prefill
    }
  }
  return withQuery("/chat", { q: text });
}

/** This tab's sessionStorage, or null where there is none (the server, a blocked browser). */
export function sessionStore(): PendingStorage | null {
  try {
    return typeof window === "undefined" ? null : window.sessionStorage;
  } catch {
    return null;
  }
}
