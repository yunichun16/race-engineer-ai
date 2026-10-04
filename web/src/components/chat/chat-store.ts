"use client";

// The page's one chat store (lib/chat/store.ts) and the composer's draft, both outside React, so
// a conversation and a half-typed question survive client navigation (even mid-answer) and the
// conversation survives a reload. Components read them with useSyncExternalStore.

import { useSyncExternalStore } from "react";
import { loadChatStatus } from "@/lib/api/chat-status";
import { callTool } from "@/lib/api/client";
import { noteUnreachable, recheckOnUnreachable } from "@/lib/api/health";
import type { ChatState } from "@/lib/chat/reducer";
import { createChatStore, type ChatStore } from "@/lib/chat/store";
import { API_URL } from "@/lib/env";

/** fetch for the chat's POST: when the server can't be reached, the health check runs again, so
 *  the page's server state follows what the question found. */
const chatFetch: typeof fetch = async (input, init) => {
  try {
    return await fetch(input, init);
  } catch (error) {
    if (!init?.signal?.aborted) noteUnreachable();
    throw error;
  }
};

export const chatStore: ChatStore = createChatStore({
  apiUrl: API_URL,
  fetch: chatFetch,
  // "Show telemetry" inside a find-mistakes card: REST explain_corner, no question used.
  callTool: (name, params, init) => recheckOnUnreachable(callTool(name, params, init)),
  // GET /api/chat/status as the page opens (M7): the limits, before anyone types.
  loadStatus: () => loadChatStatus({ baseUrl: API_URL }),
});

// In the browser the saved conversation comes back as this module loads, before the page's
// first render, so a reload shows the transcript at once rather than a frame of the empty page.
// (The server's copy of this module never has storage, so it always holds the empty state.)
if (typeof window !== "undefined") chatStore.restore();

/** The conversation, re-rendering on every change. */
export function useChat(): ChatState {
  return useSyncExternalStore(chatStore.subscribe, chatStore.getState, chatStore.getInitialState);
}

// ---- The composer's draft ----

let draft = "";
const listeners = new Set<() => void>();

/**
 * What the composer holds. Anything may fill it (?q=, "Ask about this corner", a question that
 * couldn't be sent), and it outlives the page component, so a draft survives a visit to another
 * page and back. It never sends anything by itself.
 */
export const draftStore = {
  get: (): string => draft,
  set(text: string): void {
    if (text === draft) return;
    draft = text;
    for (const listener of [...listeners]) listener();
  },
  subscribe(listener: () => void): () => void {
    listeners.add(listener);
    return () => {
      listeners.delete(listener);
    };
  },
};

const noDraft = () => "";

export function useDraft(): string {
  return useSyncExternalStore(draftStore.subscribe, draftStore.get, noDraft);
}
