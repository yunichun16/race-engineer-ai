// The chat's state outside React: one store owns the request, the stream, the reducer and the
// saved copy, so a conversation survives client navigation (even mid-answer) and reloads.
// Components read it with useSyncExternalStore(store.subscribe, store.getState,
// store.getInitialState); the page keeps one module-level instance (components/chat/chat-store.ts).
//
// Nothing here touches the DOM. fetch, storage, the clock and the REST tool call are passed in
// (with browser defaults), so the store runs under node --test as it does in the page.
//
// - The saved copy is restored on the first subscribe or action, or when the page calls
//   restore() as its module loads in the browser; never during a render. The server always has
//   the empty state (getInitialState), and useSyncExternalStore reconciles the two.
// - It is written when a question starts, on `done`, when a question fails or is cut off or
//   stopped, when an attachment finishes, and on reset; never on each text delta.
// - A question is refused while another is in flight or the conversation is blocked, and a
//   history over MAX_HISTORY_CHARS blocks the conversation without a round trip (the server's
//   limit is 512 KB).
// - M7: on the first subscribe the store reads the chat's status (GET /api/chat/status, passed
//   in as loadStatus), so a visitor the limits have closed the chat to is told before typing,
//   and no question is sent meanwhile. When the time the server gave passes, it reads the
//   status again and opens the chat if it can. `checked()` lets the page wait for that first
//   read (briefly) before it sends the landing page's question.
// - showSaved() adds a saved example answer as a turn; it is kept with the conversation and
//   never sent, since the history it carries is the server's alone.

import { ApiError } from "../api/client.ts";
import type { ChatStatus } from "../api/types.ts";
import { streamChat } from "./client.ts";
import { normaliseTurn, type ExplainCornerArgs } from "./links.ts";
import { loadChat, saveChat, sessionStorageOrNull, takePending, type KeyValueStorage } from "./persist.ts";
import {
  COPY,
  INITIAL_CHAT_STATE,
  canAsk,
  chatReducer,
  type ChatAction,
  type ChatState,
} from "./reducer.ts";
import type { SavedAnswer } from "./saved.ts";
import { MAX_HISTORY_CHARS, MAX_MESSAGE_CHARS } from "./types.ts";

/** The REST call behind "Show telemetry" (lib/api's callTool fits). */
export type ExplainCall = (
  name: "explain_corner",
  params: ExplainCornerArgs,
  init?: { signal?: AbortSignal },
) => Promise<{ summary: string; data: unknown }>;

/** Runs `run` after `ms`; the returned function cancels it. */
export type SetTimer = (run: () => void, ms: number) => () => void;

export interface ChatStoreOptions {
  fetch?: typeof fetch; // default: the global fetch, looked up when a question is sent
  storage?: KeyValueStorage | null; // default: this tab's sessionStorage; null keeps nothing
  now?: () => number;
  apiUrl?: string; // default: client.ts DEFAULT_API_URL
  callTool?: ExplainCall; // without it, attach does nothing
  newId?: () => string;
  loadStatus?: () => Promise<ChatStatus>; // GET /api/chat/status; without it, no status is read
  setTimer?: SetTimer; // default: setTimeout
}

/** What showSaved needs of a recording (lib/chat/saved.ts SavedAnswer). */
export type SavedTurn = Pick<SavedAnswer, "id" | "question" | "events" | "recorded_at">;

/** How long checked() waits for the first status before giving up on it. */
export const STATUS_WAIT_MS = 3000;

// setTimeout fires at once past 2^31 - 1 ms (about 24.8 days).
const MAX_TIMER_MS = 2_147_483_647;

const defaultTimer: SetTimer = (run, ms) => {
  const handle = setTimeout(run, Math.min(ms, MAX_TIMER_MS));
  return () => clearTimeout(handle);
};

export interface ChatStore {
  subscribe(listener: () => void): () => void;
  getState(): ChatState;
  /** The state the server renders with (useSyncExternalStore's third argument). */
  getInitialState(): ChatState;
  /** Restores the saved conversation now, if that hasn't happened yet (it otherwise happens on
   *  the first subscribe). The page calls it before its first client render, so a conversation
   *  restored from storage doesn't follow a frame of the empty page. */
  restore(): void;
  /** Sends a question; false when it can't be sent now (blank, too long, busy or blocked). */
  ask(question: string): boolean;
  stop(): void;
  /** Asks a failed last turn's question again, in its place unless the server counted it (then
   *  after it); false when it can't. */
  retry(turnId: string): boolean;
  reset(): void;
  /** Opens a corner chart under a find-mistakes card; returns the attachment id, or null. */
  attach(turnId: string, toolId: string, args: ExplainCornerArgs): string | null;
  /** The landing page's question, read and deleted once. */
  consumePending(): string | null;
  /** Resolves once the first status read has finished, or after STATUS_WAIT_MS, whichever
   *  comes first (at once when there is no status to read). */
  checked(): Promise<void>;
  /** Adds a saved example answer as a turn; false when it can't be shown now (an answer is
   *  streaming) or already is. */
  showSaved(answer: SavedTurn): boolean;
}

let idCounter = 0;

function defaultId(): string {
  // crypto.randomUUID needs a secure context, which a phone on the LAN over http isn't.
  idCounter += 1;
  return `${Date.now().toString(36)}-${idCounter.toString(36)}-${Math.random().toString(36).slice(2, 8)}`;
}

function messageOf(error: unknown): string {
  return error instanceof Error && error.message !== "" ? error.message : "The chart couldn't load.";
}

export function createChatStore(options: ChatStoreOptions = {}): ChatStore {
  const now = options.now ?? Date.now;
  const newId = options.newId ?? defaultId;
  const setTimer = options.setTimer ?? defaultTimer;
  const storage = (): KeyValueStorage | null =>
    options.storage !== undefined ? options.storage : sessionStorageOrNull();

  let state: ChatState = INITIAL_CHAT_STATE;
  let restored = false;
  let active: { id: string; controller: AbortController } | null = null;
  const loads = new Map<string, AbortController>(); // attachments being fetched
  const listeners = new Set<() => void>();
  let statusRead: Promise<void> | null = null;
  let expiry: { at: number; cancel: () => void } | null = null; // the timer for `unavailable`

  function dispatch(action: ChatAction): boolean {
    const next = chatReducer(state, action);
    if (next === state) return false;
    state = next;
    scheduleExpiry();
    for (const listener of [...listeners]) listener();
    return true;
  }

  /** Keeps one timer for the time `unavailable` ends, when it has one. */
  function scheduleExpiry(): void {
    const at = state.unavailable?.retryAt ?? null;
    if (expiry !== null && expiry.at === at) return;
    expiry?.cancel();
    expiry = null;
    if (at === null) return;
    const cancel = setTimer(() => {
      if (expiry?.at === at) expiry = null;
      void reopen();
    }, Math.max(0, at - now()));
    expiry = { at, cancel };
  }

  /** The time the server gave has passed: ask it again; with no answer, let the visitor try. */
  async function reopen(): Promise<void> {
    if (options.loadStatus) {
      try {
        dispatch({ type: "chatStatus", status: await options.loadStatus(), at: now() });
      } catch {
        // the tick below opens the chat; a question then finds out
      }
    }
    dispatch({ type: "tick", at: now() });
  }

  function readStatus(): Promise<void> {
    if (statusRead !== null) return statusRead;
    const load = options.loadStatus;
    statusRead =
      load === undefined
        ? Promise.resolve()
        : load().then(
            (status) => {
              dispatch({ type: "chatStatus", status, at: now() });
            },
            () => {
              // No status (an API from before M7, or the server is down): the page goes by its
              // health check, as it did before the limits.
            },
          );
    return statusRead;
  }

  function save(): void {
    saveChat(storage(), state);
  }

  /** An id no turn or attachment uses yet (a restored conversation brings its own ids). */
  function freshId(): string {
    const taken = new Set<string>();
    for (const turn of state.turns) {
      taken.add(turn.id);
      for (const part of turn.parts) if (part.kind === "attachment") taken.add(part.id);
    }
    let id = newId();
    while (taken.has(id)) id = newId();
    return id;
  }

  function ensureRestored(): void {
    if (restored) return;
    restored = true;
    const saved = loadChat(storage());
    if (saved !== null) dispatch({ type: "restore", state: saved });
  }

  async function run(id: string, message: string, history: unknown[], signature: string | null): Promise<void> {
    const controller = new AbortController();
    active = { id, controller };
    try {
      const events = streamChat(
        { message, history, signature },
        { signal: controller.signal, fetch: options.fetch, apiUrl: options.apiUrl },
      );
      for await (const event of events) {
        dispatch({ type: "event", id, event, at: now() });
        if (event.type === "done") save();
      }
      // Closed without `done`: a no-op when `done` came.
      if (!controller.signal.aborted && dispatch({ type: "ended", id })) save();
    } catch (error) {
      if (controller.signal.aborted) return; // stop() or reset() already ended the turn
      dispatch(error instanceof ApiError ? { type: "failed", id, error, at: now() } : { type: "ended", id });
      save();
    } finally {
      if (active?.id === id) active = null;
    }
  }

  function start(question: string, replaces?: string): boolean {
    ensureRestored();
    const text = question.trim();
    if (text === "" || text.length > MAX_MESSAGE_CHARS || !canAsk(state)) return false;
    const { history, signature } = state;
    const id = freshId();
    if (!dispatch({ type: "ask", id, question: text, at: now(), replaces })) return false;
    if (JSON.stringify(history).length > MAX_HISTORY_CHARS) {
      dispatch({ type: "failed", id, error: { status: 413, code: "history_too_large", message: COPY.tooLarge } });
      save();
      return true;
    }
    save(); // so a reload mid-answer shows the question, cut off, with Try again
    void run(id, text, history, signature);
    return true;
  }

  return {
    subscribe(listener) {
      ensureRestored();
      void readStatus();
      listeners.add(listener);
      return () => {
        listeners.delete(listener);
      };
    },

    getState: () => state,

    getInitialState: () => INITIAL_CHAT_STATE,

    restore: () => ensureRestored(),

    ask: (question) => start(question),

    stop() {
      if (active === null) return;
      const { id, controller } = active;
      active = null;
      controller.abort();
      dispatch({ type: "stopped", id });
      save();
    },

    retry(turnId) {
      ensureRestored();
      const last = state.turns[state.turns.length - 1];
      if (last === undefined || last.id !== turnId || last.phase !== "done" || !last.problem?.retryable) {
        return false;
      }
      return start(last.question, last.id);
    },

    reset() {
      ensureRestored();
      if (active !== null) {
        active.controller.abort();
        active = null;
      }
      for (const controller of loads.values()) controller.abort();
      loads.clear();
      dispatch({ type: "reset" });
      save();
    },

    attach(turnId, toolId, args) {
      ensureRestored();
      const call = options.callTool;
      if (call === undefined) return null;
      const id = freshId();
      const turn = normaliseTurn(args.corner);
      const corner = turn === null ? undefined : { driver: args.driver, lap_number: args.lap, turn };
      if (!dispatch({ type: "attach", turnId, forToolId: toolId, id, corner })) return null;
      const controller = new AbortController();
      loads.set(id, controller);
      const finished = (change: { data?: unknown; summary?: string; error?: string }): void => {
        loads.delete(id);
        if (controller.signal.aborted) return; // reset while it loaded
        dispatch({ type: "attached", turnId, id, ...change });
        save();
      };
      Promise.resolve()
        .then(() => call("explain_corner", args, { signal: controller.signal }))
        .then(
          (result) => finished({ data: result.data, summary: result.summary }),
          (error: unknown) => finished({ error: messageOf(error) }),
        );
      return id;
    },

    consumePending: () => takePending(storage()),

    checked() {
      ensureRestored();
      const read = readStatus();
      if (options.loadStatus === undefined) return read;
      return new Promise<void>((resolve) => {
        const cancel = setTimer(resolve, STATUS_WAIT_MS);
        void read.then(() => {
          cancel();
          resolve();
        });
      });
    },

    showSaved(answer) {
      ensureRestored();
      const shown = dispatch({
        type: "showSaved",
        id: answer.id,
        question: answer.question,
        events: answer.events,
        recordedAt: answer.recorded_at,
        turnId: freshId(),
      });
      if (shown) save();
      return shown;
    },
  };
}
