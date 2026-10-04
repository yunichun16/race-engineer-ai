import assert from "node:assert/strict";
import { test } from "node:test";

import { ApiError } from "../api/client.ts";
import type { ChatStatus } from "../api/types.ts";
import { PENDING_KEY, STORAGE_KEY, loadChat, saveChat } from "./persist.ts";
import { COPY, INITIAL_CHAT_STATE, attachmentsFor, replay, type ChatState, type Turn } from "./reducer.ts";
import { parseSavedAnswer } from "./saved.ts";
import { createSSEDecoder, parseChatEvent } from "./sse.ts";
import { STATUS_WAIT_MS, createChatStore, type ChatStore, type ExplainCall, type SetTimer } from "./store.ts";
import { bytes, fixtureBytes, memoryStorage, savedFixture, streamOf, type StreamProbe } from "./test-support.ts";

interface Sent {
  url: string;
  body: { message: string; history: unknown[]; signature: string | null };
  signal: AbortSignal | undefined;
}

/** A fetch that answers each call with the next responder in the queue. */
function server(...responders: (() => Response | Promise<Response>)[]) {
  const sent: Sent[] = [];
  const fetch = (async (input: string | URL | Request, init?: RequestInit) => {
    sent.push({ url: String(input), body: JSON.parse(String(init?.body)), signal: init?.signal ?? undefined });
    const next = responders.shift();
    assert.ok(next, "unexpected request");
    return next();
  }) as typeof globalThis.fetch;
  return { fetch, sent };
}

function sse(probe: StreamProbe): Response {
  return new Response(probe.stream, { headers: { "content-type": "text/event-stream" } });
}

const fixture = (name: string) => () => sse(streamOf([fixtureBytes(name)]));

function json(status: number, code: string, message: string, extra: Record<string, unknown> = {}) {
  return () =>
    new Response(JSON.stringify({ error: { code, message, ...extra } }), { status, headers: { "content-type": "application/json" } });
}

/** Resolves when the store's state passes `check` (it may already). */
function until(store: ChatStore, check: (state: ChatState) => boolean, ms = 2000): Promise<ChatState> {
  return new Promise((resolve, reject) => {
    if (check(store.getState())) return resolve(store.getState());
    const timer = setTimeout(() => {
      stop();
      reject(new Error(`timed out; state: ${JSON.stringify(store.getState()).slice(0, 400)}`));
    }, ms);
    const stop = store.subscribe(() => {
      if (check(store.getState())) {
        clearTimeout(timer);
        stop();
        resolve(store.getState());
      }
    });
  });
}

const settled = (state: ChatState) => state.turns.length > 0 && state.turns[state.turns.length - 1].phase === "done";

interface SetupExtra {
  callTool?: ExplainCall;
  storage?: ReturnType<typeof memoryStorage>;
  loadStatus?: () => Promise<ChatStatus>;
  setTimer?: SetTimer;
}

function setup(fetch: typeof globalThis.fetch, extra: SetupExtra = {}) {
  const storage = extra.storage ?? memoryStorage();
  let clock = 1000;
  let ids = 0;
  const store = createChatStore({
    fetch,
    storage,
    now: () => (clock += 10),
    apiUrl: "http://api.test",
    callTool: extra.callTool,
    newId: () => `id${++ids}`,
    loadStatus: extra.loadStatus,
    setTimer: extra.setTimer,
  });
  return { store, storage, advance: (ms: number) => (clock += ms) };
}

test("ask streams an answer, notifies subscribers and saves it", async () => {
  const { fetch, sent } = server(fixture("basic"));
  const { store, storage } = setup(fetch);
  let notified = 0;
  store.subscribe(() => notified++);
  assert.equal(store.ask("  Who made the biggest mistakes?  "), true);
  const state = await until(store, settled);
  assert.equal(state.turns[0].question, "Who made the biggest mistakes?");
  assert.equal(state.turns[0].outcome, "answered");
  assert.equal(state.questionsLeft, 7);
  assert.equal(sent.length, 1);
  assert.equal(sent[0].url, "http://api.test/api/chat");
  assert.deepEqual(sent[0].body, { message: "Who made the biggest mistakes?", history: [], signature: null });
  assert.ok(notified >= 9);
  assert.deepEqual(loadChat(storage), state);
});

test("the next question sends the history and signature from done, unchanged", async () => {
  const { fetch, sent } = server(fixture("basic"), fixture("refusal"));
  const { store } = setup(fetch);
  store.ask("First");
  const first = await until(store, settled);
  assert.equal(store.ask("Second"), true);
  await until(store, (s) => s.turns.length === 2 && settled(s));
  assert.equal(sent[1].body.history.length, 4);
  assert.deepEqual(sent[1].body.history, first.history);
  assert.equal(sent[1].body.signature, first.signature);
});

test("ask is refused when blank, too long, busy or blocked, without a request", async () => {
  const probe = streamOf([], { hold: true });
  const { fetch, sent } = server(() => sse(probe));
  const { store } = setup(fetch);
  assert.equal(store.ask("   "), false);
  assert.equal(store.ask("x".repeat(2001)), false);
  assert.equal(store.ask("x".repeat(2000)), true);
  assert.equal(store.ask("Another"), false); // one in flight
  store.stop();
  assert.equal(sent.length, 1);
  const blocked = server(json(409, "turn_limit", "limit"));
  const second = setup(blocked.fetch);
  second.store.ask("Ninth");
  const state = await until(second.store, settled);
  assert.equal(state.blocked?.code, "turn_limit");
  assert.equal(second.store.ask("Tenth"), false);
  assert.equal(blocked.sent.length, 1);
});

test("a pre-stream refusal blocks the conversation and is saved", async () => {
  const { fetch } = server(json(400, "invalid_history", "The conversation history doesn't match its signature."));
  const { store, storage } = setup(fetch);
  store.ask("Q");
  const state = await until(store, settled);
  assert.deepEqual(state.blocked, { code: "invalid_history", message: COPY.cantContinue });
  assert.equal(state.turns[0].outcome, "error");
  assert.deepEqual(loadChat(storage)?.blocked, state.blocked);
});

test("a network error marks the turn for Try again, which replaces it", async () => {
  const { fetch, sent } = server(() => {
    throw new TypeError("Failed to fetch");
  }, fixture("basic"));
  const { store } = setup(fetch);
  store.ask("Q");
  let state = await until(store, settled);
  assert.deepEqual(state.turns[0].problem, { code: "unreachable", message: "Can't reach the analysis server.", retryable: true });
  assert.equal(store.retry("nope"), false);
  assert.equal(store.retry(state.turns[0].id), true);
  state = await until(store, (s) => s.turns[0].outcome === "answered");
  assert.equal(state.turns.length, 1);
  assert.equal(state.turns[0].question, "Q");
  assert.equal(sent.length, 2);
  assert.equal(store.retry(state.turns[0].id), false); // answered: nothing to retry
});

test("a stream that closes without done is cut off, keeps the old history and can be retried", async () => {
  const { fetch, sent } = server(fixture("basic"), fixture("no-done"), fixture("refusal"));
  const { store, storage } = setup(fetch);
  store.ask("First");
  const first = await until(store, settled);
  store.ask("Second");
  const cut = await until(store, (s) => s.turns.length === 2 && settled(s));
  assert.equal(cut.turns[1].outcome, "interrupted");
  assert.equal(cut.history, first.history);
  assert.equal(loadChat(storage)?.turns[1].outcome, "interrupted");
  assert.equal(store.retry(cut.turns[1].id), true);
  await until(store, (s) => s.turns.length === 2 && s.turns[1].outcome === "refused");
  assert.deepEqual(sent[2].body.history, first.history);
});

test("a stream error mid-answer keeps the text and stores the history done sent", async () => {
  const { fetch } = server(fixture("error-timeout"));
  const { store } = setup(fetch);
  store.ask("Q");
  const state = await until(store, settled);
  assert.equal(state.turns[0].problem?.code, "timeout");
  assert.equal(state.history.length, 3);
});

test("Try again after a counted mid-answer error keeps that turn and sends the server's history", async () => {
  const { fetch, sent } = server(fixture("error-timeout"), fixture("basic"));
  const { store } = setup(fetch);
  store.ask("Q");
  const failed = await until(store, settled);
  assert.equal(failed.turns[0].counted, true);
  assert.equal(store.retry(failed.turns[0].id), true);
  const state = await until(store, (s) => s.turns.length === 2 && settled(s));
  assert.deepEqual(state.turns.map((t) => t.question), ["Q", "Q"]);
  assert.equal(state.turns[0], failed.turns[0]); // still shown, unchanged
  assert.deepEqual(sent[1].body.history, failed.history); // the history as the server last sent it
});

test("a body that breaks mid-stream cuts the turn off and keeps the partial text", async () => {
  let pulls = 0;
  const broken = () =>
    new Response(
      new ReadableStream<Uint8Array>({
        pull(controller) {
          pulls += 1;
          if (pulls === 1) controller.enqueue(bytes('event: text\ndata: {"delta": "Half"}\n\n'));
          else controller.error(new TypeError("network error"));
        },
      }),
      { headers: { "content-type": "text/event-stream" } },
    );
  const { fetch } = server(broken);
  const { store, storage } = setup(fetch);
  store.ask("Q");
  const state = await until(store, settled);
  assert.equal(state.turns[0].outcome, "interrupted");
  assert.deepEqual(state.turns[0].problem, { code: "interrupted", message: COPY.cutOff, retryable: true });
  assert.deepEqual(state.turns[0].parts, [{ kind: "text", text: "Half" }]);
  assert.deepEqual(state.history, []);
  assert.equal(loadChat(storage)?.turns[0].outcome, "interrupted");
});

test("replay gives the turn a live answer gave, apart from its timing", async () => {
  for (const name of ["basic", "retry", "refusal", "error-timeout", "no-done"]) {
    const data = fixtureBytes(name);
    const { fetch } = server(() => sse(streamOf([data.subarray(0, 3), data.subarray(3, 400), data.subarray(400)])));
    const store = createChatStore({ fetch, storage: null, newId: () => "replay" });
    store.ask("Q?");
    const live = (await until(store, settled)).turns[0];
    const decoder = createSSEDecoder();
    const events = [...decoder.feed(data), ...decoder.end()].map((m) => parseChatEvent(m.event, m.data));
    const replayed = replay(events.filter((e) => e !== null), "Q?");
    const untimed = (turn: Turn): Turn => ({ ...turn, startedAt: 0, meta: turn.meta && { ...turn.meta, ms: 0 } });
    assert.deepEqual(untimed(replayed), untimed(live), name);
  }
});

test("stop ends the answer at once, cancels the body and doesn't count the question", async () => {
  const probe = streamOf([bytes('event: text\ndata: {"delta": "Half an answer"}\n\n')], { hold: true });
  const { fetch, sent } = server(fixture("basic"), () => sse(probe), fixture("refusal"));
  const { store, storage } = setup(fetch);
  store.ask("First");
  const first = await until(store, settled);
  store.ask("Second");
  await until(store, (s) => s.turns.length === 2 && s.turns[1].parts.length > 0);
  store.stop();
  const state = store.getState();
  assert.equal(state.turns[1].outcome, "stopped");
  assert.equal(state.history, first.history);
  assert.equal(state.signature, first.signature);
  assert.equal(sent[1].signal?.aborted, true);
  await new Promise((resolve) => setTimeout(resolve, 10));
  assert.equal(probe.cancelled(), true);
  assert.equal(loadChat(storage)?.turns[1].outcome, "stopped");
  assert.equal(store.getState(), state); // nothing arrived after the stop
  assert.equal(store.ask("Third"), true);
  await until(store, (s) => s.turns.length === 3 && settled(s));
});

test("stop while the request is still connecting", async () => {
  const { fetch } = server(
    () =>
      new Promise<Response>(() => {
        // never answers: the abort is what ends it
      }),
  );
  const { store } = setup(fetch);
  store.ask("Q");
  store.stop();
  assert.equal(store.getState().turns[0].outcome, "stopped");
});

test("reset aborts the answer in flight and starts over, saved", async () => {
  const probe = streamOf([], { hold: true });
  const { fetch } = server(() => sse(probe));
  const { store, storage } = setup(fetch);
  store.ask("Q");
  store.reset();
  assert.equal(store.getState(), INITIAL_CHAT_STATE);
  await new Promise((resolve) => setTimeout(resolve, 10));
  assert.equal(store.getState(), INITIAL_CHAT_STATE);
  assert.deepEqual(loadChat(storage), INITIAL_CHAT_STATE);
});

test("the saved conversation is restored on first subscribe, cut-off turns marked", async () => {
  const storage = memoryStorage();
  const first = setup(server(fixture("basic")).fetch, { storage });
  first.store.ask("First");
  const answered = await until(first.store, settled);
  // A second question was in flight when the tab reloaded: it was saved when it started.
  const probe = streamOf([], { hold: true });
  const second = setup(server(() => sse(probe)).fetch, { storage });
  second.store.ask("Second");
  assert.equal(loadChat(storage)?.turns[1].phase, "sending");

  const { store } = setup(server(fixture("refusal")).fetch, { storage });
  assert.equal(store.getState(), INITIAL_CHAT_STATE); // nothing read before subscribing
  const unsubscribe = store.subscribe(() => {});
  const state = store.getState();
  assert.equal(state.turns.length, 2);
  assert.equal(state.turns[1].outcome, "interrupted");
  assert.deepEqual(state.history, answered.history);
  assert.equal(store.getInitialState(), INITIAL_CHAT_STATE);
  unsubscribe();
  second.store.stop();
});

test("asking before subscribing restores first, so the new turn isn't overwritten", async () => {
  const storage = memoryStorage();
  const first = setup(server(fixture("basic")).fetch, { storage });
  first.store.ask("First");
  await until(first.store, settled);
  const { store } = setup(server(fixture("refusal")).fetch, { storage });
  store.ask("Second");
  store.subscribe(() => {});
  const state = await until(store, (s) => s.turns.length === 2 && settled(s));
  assert.deepEqual(state.turns.map((t) => t.question), ["First", "Second"]);
});

test("a history over the client's limit blocks without a request", async () => {
  const storage = memoryStorage();
  const huge: ChatState = { ...INITIAL_CHAT_STATE, history: [{ role: "user", content: "x".repeat(500_001) }], signature: "s" };
  saveChat(storage, huge);
  const { fetch, sent } = server();
  const { store } = setup(fetch, { storage });
  assert.equal(store.ask("Q"), true);
  const state = store.getState();
  assert.deepEqual(state.blocked, { code: "history_too_large", message: COPY.tooLarge });
  assert.equal(sent.length, 0);
});

test("consumePending reads the landing page's question once", () => {
  const storage = memoryStorage();
  storage.map.set(PENDING_KEY, "Summarise the 2023 Monaco Grand Prix.");
  const { store } = setup(server().fetch, { storage });
  assert.equal(store.consumePending(), "Summarise the 2023 Monaco Grand Prix.");
  assert.equal(store.consumePending(), null);
});

test("attach opens a corner chart through REST and leaves the conversation alone", async () => {
  const calls: unknown[] = [];
  const corner = { driver: "AAA", lap_number: 9, turn: "3" };
  let fail = false;
  const callTool: ExplainCall = async (name, params) => {
    calls.push([name, params]);
    if (fail) throw new ApiError(422, "invalid_input", "AAA didn't drive lap 99.");
    return { summary: "Lap 9, turn 3", data: corner };
  };
  const { store, storage } = setup(server(fixture("basic")).fetch, { callTool });
  store.ask("Q");
  const before = await until(store, settled);
  const turnId = before.turns[0].id;
  const args = { event: "Sample Grand Prix", driver: "AAA", lap: 9, corner: "3", year: 2026, session: "Q" as const };
  const id = store.attach(turnId, "toolu_basic_0", args);
  assert.ok(id);
  assert.equal(attachmentsFor(store.getState().turns[0], "toolu_basic_0")[0].state, "loading");
  assert.deepEqual(attachmentsFor(store.getState().turns[0], "toolu_basic_0")[0].corner, corner);
  let state = await until(store, (s) => attachmentsFor(s.turns[0], "toolu_basic_0")[0]?.state === "ok");
  assert.deepEqual(calls, [["explain_corner", args]]);
  assert.equal(attachmentsFor(state.turns[0], "toolu_basic_0")[0].data, corner);
  assert.equal(state.history, before.history);
  assert.equal(state.questionsLeft, before.questionsLeft);
  assert.equal(attachmentsFor(loadChat(storage)?.turns[0] ?? state.turns[0], "toolu_basic_0")[0].state, "ok");

  fail = true;
  store.attach(turnId, "toolu_basic_0", { ...args, lap: 99 });
  state = await until(store, (s) => attachmentsFor(s.turns[0], "toolu_basic_0")[1]?.state === "error");
  assert.equal(attachmentsFor(state.turns[0], "toolu_basic_0")[1].error, "AAA didn't drive lap 99.");
  assert.equal(store.attach("nope", "toolu_basic_0", args), null);

  // The corner is named as the explorer's `explain` key writes it: "T9a" is turn 9A.
  store.attach(turnId, "toolu_basic_0", { ...args, corner: "T9a" });
  assert.deepEqual(attachmentsFor(store.getState().turns[0], "toolu_basic_0")[2].corner, {
    driver: "AAA",
    lap_number: 9,
    turn: "9A",
  });
});

test("restore() brings the saved conversation back before anything subscribes, once", () => {
  const storage = memoryStorage();
  const saved: ChatState = { ...INITIAL_CHAT_STATE, turns: [], history: [{ role: "user", content: "x" }], signature: "v.s" };
  saveChat(storage, saved);
  const store = createChatStore({ storage, fetch: (() => Promise.reject(new Error("no"))) as typeof fetch });
  assert.equal(store.getState(), INITIAL_CHAT_STATE);
  store.restore();
  assert.equal(store.getState().signature, "v.s");
  saveChat(storage, INITIAL_CHAT_STATE);
  store.restore();
  store.subscribe(() => {})();
  assert.equal(store.getState().signature, "v.s");
});

test("attach without a REST call does nothing; a reset drops a loading attachment", async () => {
  const { store } = setup(server(fixture("basic")).fetch);
  store.ask("Q");
  const state = await until(store, settled);
  assert.equal(store.attach(state.turns[0].id, "toolu_basic_0", { event: "E", driver: "AAA", lap: 1, corner: 1 }), null);

  let release: (value: { summary: string; data: unknown }) => void = () => {};
  const callTool: ExplainCall = () => new Promise((resolve) => (release = resolve));
  const other = setup(server(fixture("basic")).fetch, { callTool });
  other.store.ask("Q");
  const answered = await until(other.store, settled);
  other.store.attach(answered.turns[0].id, "toolu_basic_0", { event: "E", driver: "AAA", lap: 1, corner: 1 });
  other.store.reset();
  release({ summary: "late", data: {} });
  await new Promise((resolve) => setTimeout(resolve, 10));
  assert.equal(other.store.getState(), INITIAL_CHAT_STATE);
});

test("a full storage keeps the conversation without chart data", async () => {
  const storage = memoryStorage(3000); // the basic answer's state is about 9 KB with its chart
  const { store } = setup(server(fixture("basic")).fetch, { storage });
  store.ask("Q");
  await until(store, settled);
  const saved = JSON.parse(storage.map.get(STORAGE_KEY) ?? "null");
  assert.ok(saved);
  const tool = saved.state.turns[0].parts.find((p: { kind: string }) => p.kind === "tool");
  assert.equal(tool.result.chart.dropped, true);
  assert.equal(saved.state.signature, store.getState().signature);
});

// ---- M7: the chat's status, the limits and saved answers ----

const OPEN: ChatStatus = {
  available: true,
  mode: "anthropic",
  reason: null,
  retry_after_s: null,
  quota: { hour_left: 3, day_left: 21, per_hour: 3, per_day: 25 },
  visitor: "3fa2c1",
  saved: [],
};
const PAUSED: ChatStatus = { ...OPEN, available: false, reason: "paused" };

/** Timers the test fires by hand: each pending one with its delay. */
function manualTimers() {
  const pending: { run: () => void; ms: number; live: boolean }[] = [];
  const setTimer: SetTimer = (run, ms) => {
    const timer = { run, ms, live: true };
    pending.push(timer);
    return () => {
      timer.live = false;
    };
  };
  const live = () => pending.filter((t) => t.live);
  const fire = (timer: { run: () => void; live: boolean }) => {
    timer.live = false;
    timer.run();
  };
  return { setTimer, live, fire };
}

/** Statuses in turn, then the last one again; counts the reads. */
function statuses(...answers: (ChatStatus | Error)[]) {
  let reads = 0;
  const loadStatus = async () => {
    const next = answers[Math.min(reads, answers.length - 1)];
    reads++;
    if (next instanceof Error) throw next;
    return next;
  };
  return { loadStatus, reads: () => reads };
}

const flush = () => new Promise((resolve) => setTimeout(resolve, 0));

test("the status is read once, on the first subscribe, and closes the chat before anyone types", async () => {
  const { loadStatus, reads } = statuses(PAUSED);
  const { fetch, sent } = server();
  const { store } = setup(fetch, { loadStatus });
  assert.equal(reads(), 0); // nothing before the page subscribes
  store.subscribe(() => {});
  store.subscribe(() => {});
  const state = await until(store, (s) => s.unavailable !== null);
  assert.equal(reads(), 1);
  assert.deepEqual(state.unavailable, { code: "chat_paused", message: "", retryAt: null });
  assert.equal(store.ask("Summarise the 2023 Monaco Grand Prix."), false);
  assert.equal(sent.length, 0);
});

test("a pending question isn't sent while the chat is unavailable: the page waits for the status first", async () => {
  const storage = memoryStorage();
  storage.map.set(PENDING_KEY, "Summarise the 2023 Monaco Grand Prix.");
  const { fetch, sent } = server();
  const capped: ChatStatus = { ...OPEN, available: false, reason: "daily_cap", retry_after_s: 3600 };
  const { store } = setup(fetch, { storage, loadStatus: statuses(capped).loadStatus, setTimer: manualTimers().setTimer });
  const pending = store.consumePending();
  await store.checked();
  assert.equal(store.getState().unavailable?.code, "daily_cap");
  assert.equal(store.ask(pending ?? ""), false);
  assert.equal(sent.length, 0);
});

test("checked() resolves after a failed status too, and gives up on a hung one after STATUS_WAIT_MS", async () => {
  const failing = setup(server().fetch, { loadStatus: statuses(new ApiError(404, "not_found", "Not Found")).loadStatus });
  await failing.store.checked();
  assert.equal(failing.store.getState().unavailable, null);

  const timers = manualTimers();
  const hung = setup(server().fetch, { loadStatus: () => new Promise<ChatStatus>(() => {}), setTimer: timers.setTimer });
  let done = false;
  void hung.store.checked().then(() => (done = true));
  await flush();
  assert.equal(done, false);
  const wait = timers.live().find((t) => t.ms === STATUS_WAIT_MS);
  assert.ok(wait);
  timers.fire(wait);
  await flush();
  assert.equal(done, true);
  // Without a status to read there is nothing to wait for.
  await setup(server().fetch).store.checked();
});

test("a limit's 429 before the stream: unavailable, the turn gone, nothing counted; when the wait ends the status is read again", async () => {
  const timers = manualTimers();
  const { loadStatus, reads } = statuses(OPEN, OPEN);
  // The basic answer as a server with limits on sends it: done carries the visitor's quota.
  const limitedBasic = new TextDecoder()
    .decode(fixtureBytes("basic"))
    .replace('"request_id": "0ebc385d153c"}', '"request_id": "0ebc385d153c", "quota": {"hour_left": 0, "day_left": 22}}');
  assert.ok(limitedBasic.includes('"quota"'));
  const { fetch, sent } = server(
    () => sse(streamOf([bytes(limitedBasic)])),
    json(429, "rate_limited", "That's 3 questions in the last hour.", { retry_after_s: 1380, limit: "hour" }),
    fixture("refusal"),
  );
  const { store, storage, advance } = setup(fetch, { loadStatus, setTimer: timers.setTimer });
  store.subscribe(() => {});
  store.ask("First");
  const first = await until(store, settled);
  assert.deepEqual(first.quota, { hour_left: 0, day_left: 22, per_hour: 3, per_day: 25 });
  assert.equal(store.ask("Summarise the 2023 Monaco Grand Prix."), true);
  const limited = await until(store, (s) => s.unavailable !== null);
  assert.equal(limited.turns.length, 1); // the refused question isn't left in the transcript
  assert.equal(limited.unavailable?.question, "Summarise the 2023 Monaco Grand Prix.");
  assert.equal(limited.unavailable?.limit, "hour");
  assert.equal(limited.unavailable?.max, 3);
  assert.equal(limited.history, first.history);
  assert.equal(limited.questionsLeft, first.questionsLeft);
  assert.deepEqual(loadChat(storage)?.turns.map((t) => t.question), ["First"]);
  assert.equal(store.ask("Another"), false);
  assert.equal(sent.length, 2);

  // One timer for the end of the wait, 1,380 s after the refusal.
  const expiry = timers.live().find((t) => t.ms > 1_000_000);
  assert.ok(expiry);
  assert.ok(Math.abs(expiry.ms - 1_380_000) <= 50);
  advance(1_380_000);
  timers.fire(expiry);
  const open = await until(store, (s) => s.unavailable === null);
  assert.equal(reads(), 2);
  assert.equal(open.turns.length, 1);
  assert.equal(store.ask("Second"), true);
  await until(store, (s) => s.turns.length === 2 && settled(s));
  assert.equal(sent[2].body.signature, first.signature);
});

test("when the wait ends and the status can't be read, the chat opens and a question finds out", async () => {
  const timers = manualTimers();
  const { loadStatus } = statuses(OPEN, new ApiError(0, "unreachable", "x"));
  const { fetch } = server(json(503, "chat_unavailable", "The limits store can't be reached.", { retry_after_s: 60 }));
  const { store, advance } = setup(fetch, { loadStatus, setTimer: timers.setTimer });
  store.subscribe(() => {});
  await store.checked();
  store.ask("Q");
  await until(store, (s) => s.unavailable?.code === "chat_unavailable");
  const expiry = timers.live().find((t) => t.ms > 50_000);
  assert.ok(expiry);
  advance(60_000);
  timers.fire(expiry);
  await until(store, (s) => s.unavailable === null);
});

test("saved turns survive a reload and are never sent with the next question", async () => {
  const saved = parseSavedAnswer(savedFixture("saved-basic"));
  assert.ok(saved);
  const storage = memoryStorage();
  const first = setup(server(fixture("basic")).fetch, { storage });
  first.store.ask("First");
  const answered = await until(first.store, settled);
  assert.equal(first.store.showSaved(saved), true);
  assert.equal(first.store.showSaved(saved), false); // already shown
  const withSaved = first.store.getState();
  assert.equal(withSaved.turns.length, 2);
  assert.deepEqual(withSaved.turns[1].saved, { id: saved.id, recordedAt: saved.recorded_at });

  // Reload: the saved turn is back, with its chart.
  const { fetch, sent } = server(fixture("refusal"));
  const { store } = setup(fetch, { storage });
  store.subscribe(() => {});
  const restored = store.getState();
  assert.deepEqual(restored.turns, withSaved.turns);
  // Asking sends the live conversation's history and signature only.
  assert.equal(store.ask("Second"), true);
  await until(store, (s) => s.turns.length === 3 && settled(s));
  assert.deepEqual(sent[0].body.history, answered.history);
  assert.equal(sent[0].body.signature, answered.signature);
});

test("showSaved does nothing while an answer streams", async () => {
  const saved = parseSavedAnswer(savedFixture("saved-basic"));
  assert.ok(saved);
  const probe = streamOf([], { hold: true });
  const { store } = setup(server(() => sse(probe)).fetch);
  store.ask("Q");
  assert.equal(store.showSaved(saved), false);
  store.stop();
  assert.equal(store.showSaved(saved), true);
});

test("a new conversation keeps the visitor's limits", async () => {
  const { store } = setup(server().fetch, { loadStatus: statuses(PAUSED).loadStatus });
  await store.checked();
  store.reset();
  assert.equal(store.getState().unavailable?.code, "chat_paused");
  assert.equal(store.ask("Q"), false);
});
