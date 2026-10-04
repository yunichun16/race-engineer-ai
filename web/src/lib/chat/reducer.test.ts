import assert from "node:assert/strict";
import { test } from "node:test";

import type { ChatStatus } from "../api/types.ts";
import {
  COPY,
  INITIAL_CHAT_STATE,
  LIMIT_CODES,
  answerText,
  attachmentsFor,
  canAsk,
  chartParts,
  chatReducer,
  clockTime,
  dailyMeter,
  preStreamOutcome,
  replay,
  savedNote,
  unavailableCopy,
  type ChatAction,
  type ChatState,
  type Part,
  type ToolPart,
  type Turn,
  type Unavailable,
} from "./reducer.ts";
import { parseSavedAnswer } from "./saved.ts";
import { createSSEDecoder, parseChatEvent } from "./sse.ts";
import { fixtureBytes, savedFixture } from "./test-support.ts";
import type { ChatEvent, DoneEvent } from "./types.ts";

function eventsOf(name: string): ChatEvent[] {
  const decoder = createSSEDecoder();
  const messages = [...decoder.feed(fixtureBytes(name)), ...decoder.end()];
  return messages.map((m) => {
    const event = parseChatEvent(m.event, m.data);
    assert.ok(event, `${name}: ${m.event}`);
    return event;
  });
}

function run(state: ChatState, actions: ChatAction[]): ChatState {
  return actions.reduce(chatReducer, state);
}

/** Asks `question` as turn `id` at time 1000, then feeds `events` one millisecond apart. */
function answer(state: ChatState, id: string, events: ChatEvent[], question = "Q?"): ChatState {
  const actions: ChatAction[] = [{ type: "ask", id, question, at: 1000 }];
  events.forEach((event, i) => actions.push({ type: "event", id, event, at: 1001 + i }));
  return run(state, actions);
}

function only(state: ChatState): Turn {
  assert.equal(state.turns.length, 1);
  return state.turns[0];
}

function texts(parts: Part[]): string[] {
  return parts.filter((p) => p.kind === "text").map((p) => (p as { text: string }).text);
}

function kinds(parts: Part[]): string[] {
  return parts.map((p) => (p.kind === "tool" ? `tool:${p.id}` : p.kind));
}

const done = (over: Partial<DoneEvent> = {}): DoneEvent => ({
  type: "done",
  history: [{ role: "user", content: "Q?" }],
  signature: "sig-1",
  questions_used: 1,
  questions_left: 7,
  stop_reason: "end_turn",
  model: "scripted",
  usage: { input: 1, output: 2, cache_read: 3, cache_write: 4 },
  cost_usd: 0.01,
  request_id: "r1",
  quota: null,
  ...over,
});

test("ask adds a sending turn; the first event makes it streaming", () => {
  let state = chatReducer(INITIAL_CHAT_STATE, { type: "ask", id: "t1", question: "Q?", at: 5 });
  assert.deepEqual(only(state), {
    id: "t1",
    question: "Q?",
    startedAt: 5,
    parts: [],
    notes: [],
    phase: "sending",
  });
  assert.equal(canAsk(state), false);
  state = chatReducer(state, { type: "event", id: "t1", event: { type: "status", text: "Looking" }, at: 6 });
  assert.equal(only(state).phase, "streaming");
  assert.deepEqual(only(state).notes, ["Looking"]);
});

test("the usual order: status, text, tool call and result, text, done", () => {
  const events = eventsOf("basic");
  const state = answer(INITIAL_CHAT_STATE, "t1", events);
  const turn = only(state);
  assert.equal(turn.phase, "done");
  assert.equal(turn.outcome, "answered");
  assert.equal(turn.problem, undefined);
  assert.deepEqual(turn.notes, ["Looking for the flagged corners"]);
  assert.deepEqual(kinds(turn.parts), ["text", "tool:toolu_basic_0", "text"]);
  assert.deepEqual(texts(turn.parts), [
    "Checking the Sample Grand Prix with find_mistakes.",
    "AAA lost the most: 0.65 s at turn 3 on lap 9 🏁, a track-limits moment named by race control " +
      "(café au lait).\n\n- BBB: 0.31 s at turn 7, é escaped.",
  ]);
  const tool = turn.parts[1] as ToolPart;
  assert.equal(tool.name, "find_mistakes");
  assert.deepEqual(tool.input, { event: "Sample Grand Prix", year: 2026, session: "Q" });
  assert.equal(tool.result?.is_error, false);
  assert.equal(tool.result?.chart?.bundle, "find-mistakes");
  assert.equal(turn.meta?.model, "scripted");
  assert.equal(turn.meta?.ms, events.length); // done came at 1000 + events.length
  assert.equal(turn.meta?.questions_left, 7);
  assert.deepEqual(chartParts(turn).map((p) => p.id), ["toolu_basic_0"]);
  assert.equal(canAsk(state), true);
});

test("text interleaved with tools keeps its place, and tool results find their calls", () => {
  const events: ChatEvent[] = [
    { type: "text", delta: "One " },
    { type: "text", delta: "two." },
    { type: "tool_call", id: "a", name: "find_session", input: { query: "x" } },
    { type: "tool_call", id: "b", name: "find_mistakes", input: {} },
    { type: "tool_result", id: "b", name: "find_mistakes", is_error: true, summary: "bad", chart: null },
    { type: "tool_result", id: "a", name: "find_session", is_error: false, summary: "ok", chart: null },
    { type: "text", delta: "Three." },
    { type: "tool_result", id: "z", name: "list_sessions", is_error: false, summary: "orphan", chart: null },
    { type: "text", delta: "" },
    { type: "text", delta: "Four." },
  ];
  const turn = only(answer(INITIAL_CHAT_STATE, "t1", events));
  assert.deepEqual(kinds(turn.parts), ["text", "tool:a", "tool:b", "text", "tool:z", "text"]);
  assert.deepEqual(texts(turn.parts), ["One two.", "Three.", "Four."]);
  const [, a, b, , z] = turn.parts as ToolPart[];
  assert.equal(a.result?.summary, "ok");
  assert.equal(b.result?.is_error, true);
  assert.deepEqual(z.input, {}); // a result without a call still shows
  assert.equal(z.result?.summary, "orphan");
  assert.equal(turn.phase, "streaming");
  assert.equal(answerText(turn), "One two.\n\nThree.\n\nFour.");
});

test("retry drops the text after the last tool result and notes the retry", () => {
  const turn = only(answer(INITIAL_CHAT_STATE, "t1", eventsOf("retry")));
  assert.deepEqual(kinds(turn.parts), ["text", "tool:toolu_retry_0", "text", "tool:toolu_retry_1", "text"]);
  assert.deepEqual(texts(turn.parts), [
    "Checking the session.",
    "Asking for the mistakes again.",
    "AAA lost the most.",
  ]);
  assert.ok(!JSON.stringify(turn.parts).includes("garbled part"));
  assert.deepEqual(turn.notes, ["Finding the session", COPY.askingAgain, "Looking for the flagged corners"]);
  assert.equal(turn.outcome, "answered");
});

test("retry with no tool result yet drops all of the turn's text", () => {
  const turn = only(
    answer(INITIAL_CHAT_STATE, "t1", [
      { type: "text", delta: "First try" },
      { type: "tool_call", id: "a", name: "find_session", input: {} }, // no result: not an anchor
      { type: "text", delta: " more" },
      { type: "retry", message: "garbled" },
      { type: "text", delta: "Second try" },
    ]),
  );
  assert.deepEqual(kinds(turn.parts), ["tool:a", "text"]);
  assert.deepEqual(texts(turn.parts), ["Second try"]);
});

test("refusal drops the text since the last tool result and marks the turn refused", () => {
  const events = eventsOf("refusal");
  const state = answer(INITIAL_CHAT_STATE, "t1", events);
  const turn = only(state);
  assert.deepEqual(turn.parts, []);
  assert.equal(turn.outcome, "refused");
  assert.deepEqual(turn.problem, {
    code: "refusal",
    message: "Claude declined to answer this. Try asking another way.",
    retryable: false,
  });
  assert.equal(turn.phase, "done");
  // The question made no progress: the history comes back empty and isn't counted.
  assert.deepEqual(state.history, []);
  assert.equal(state.questionsLeft, 8);

  const after = only(
    answer(INITIAL_CHAT_STATE, "t2", [
      { type: "text", delta: "Kept." },
      { type: "tool_call", id: "a", name: "find_session", input: {} },
      { type: "tool_result", id: "a", name: "find_session", is_error: false, summary: "s", chart: null },
      { type: "text", delta: "Dropped." },
      { type: "refusal", message: "", category: "cyber" },
    ]),
  );
  assert.deepEqual(texts(after.parts), ["Kept."]);
  assert.equal(after.problem?.message, COPY.refused);
});

test("an error after text keeps the text and is retryable by code; done keeps the outcome", () => {
  const state = answer(INITIAL_CHAT_STATE, "t1", eventsOf("error-timeout"));
  const turn = only(state);
  assert.deepEqual(texts(turn.parts), ["Checking.", "AAA lost the most"]);
  assert.equal(turn.outcome, "error");
  assert.deepEqual(turn.problem, {
    code: "timeout",
    message: "The answer took longer than 120 seconds.",
    retryable: true,
  });
  assert.equal(turn.phase, "done");
  // The completed tool step counted the question: the server's history is stored as sent.
  assert.equal(state.history.length, 3);
  assert.equal(state.questionsLeft, 7);
});

test("stream error codes: retryable, edit-only and the configured-server copy", () => {
  const problem = (code: string, message = "server words") =>
    only(answer(INITIAL_CHAT_STATE, "t", [{ type: "error", code, message }])).problem;
  for (const code of ["busy", "upstream_busy", "upstream_unavailable", "upstream_unreachable", "timeout", "internal_error"]) {
    assert.deepEqual(problem(code), { code, message: "server words", retryable: true }, code);
  }
  for (const code of ["truncated", "step_limit", "too_costly", "bad_request", "something_new"]) {
    assert.deepEqual(problem(code), { code, message: "server words", retryable: false }, code);
  }
  assert.deepEqual(problem("chat_not_configured"), {
    code: "chat_not_configured",
    message: COPY.notConfigured,
    retryable: false,
  });
  assert.equal(problem("timeout", "")?.message, COPY.streamError);
});

test("done stores the history, signature and questions left exactly as sent", () => {
  const history = [{ role: "user", content: "Q?" }, { role: "assistant", content: [{ type: "text", text: "A" }] }];
  const event = done({ history, signature: "abc.def", questions_left: 3 });
  const state = answer(INITIAL_CHAT_STATE, "t1", [event]);
  assert.equal(state.history, history); // the same array, untouched
  assert.equal(state.signature, "abc.def");
  assert.equal(state.questionsLeft, 3);
  assert.equal(state.blocked, null);
  assert.deepEqual(only(state).meta, { model: "scripted", ms: 1, cost_usd: 0.01, questions_left: 3 });
});

test("turn_limit from done: the eighth answer blocks the conversation", () => {
  const state = answer(INITIAL_CHAT_STATE, "t8", [{ type: "text", delta: "A" }, done({ questions_left: 0 })]);
  assert.deepEqual(state.blocked, { code: "turn_limit", message: COPY.turnLimit });
  assert.equal(only(state).outcome, "answered");
  assert.equal(canAsk(state), false);
  assert.equal(chatReducer(state, { type: "ask", id: "t9", question: "more", at: 0 }), state);
});

test("turn_limit before the stream blocks too, and sets no questions left", () => {
  let state = chatReducer(INITIAL_CHAT_STATE, { type: "ask", id: "t1", question: "Q?", at: 0 });
  state = chatReducer(state, {
    type: "failed",
    id: "t1",
    error: { status: 409, code: "turn_limit", message: "This conversation has reached its limit of 8 questions; start a new one." },
  });
  assert.deepEqual(state.blocked, { code: "turn_limit", message: COPY.turnLimit });
  assert.equal(state.questionsLeft, 0);
  assert.deepEqual(only(state).problem, { code: "turn_limit", message: COPY.turnLimit, retryable: false });
  assert.equal(only(state).outcome, "error");
  assert.equal(only(state).phase, "done");
});

test("failed maps each pre-stream code to a block or a retryable turn", () => {
  const cases: [number, string, string, string, boolean, boolean][] = [
    // status, code, server message, expected message, blocks, retryable
    [503, "chat_disabled", "The chat is off on this server.", "The chat is off on this server.", true, false],
    [503, "busy", "Try again shortly.", "Try again shortly.", false, true],
    [400, "invalid_history", "x", COPY.cantContinue, true, false],
    [409, "conversation_expired", "x", COPY.cantContinue, true, false],
    [413, "history_too_large", "x", COPY.tooLarge, true, false],
    [422, "invalid_request", "message: too long", "That question couldn't be sent: message: too long", false, false],
    [0, "unreachable", "Can't reach the analysis server.", "Can't reach the analysis server.", false, true],
    [200, "bad_payload", "The chat server didn't answer with a stream.", "The chat server didn't answer with a stream.", false, true],
    [500, "http_500", "", "The server answered 500.", false, false],
  ];
  for (const [status, code, message, expected, blocks, retryable] of cases) {
    const outcome = preStreamOutcome({ status, code, message });
    assert.deepEqual(outcome.problem, { code, message: expected, retryable }, code);
    assert.deepEqual(outcome.block, blocks ? { code, message: expected } : null, code);
    let state = chatReducer(INITIAL_CHAT_STATE, { type: "ask", id: "t", question: "Q?", at: 0 });
    state = chatReducer(state, { type: "failed", id: "t", error: { status, code, message } });
    assert.equal(state.blocked?.code ?? null, blocks ? code : null, code);
    assert.equal(canAsk(state), !blocks, code);
  }
});

test("failed with aborted is a stop", () => {
  let state = chatReducer(INITIAL_CHAT_STATE, { type: "ask", id: "t", question: "Q?", at: 0 });
  state = chatReducer(state, { type: "failed", id: "t", error: { status: 0, code: "aborted", message: "" } });
  assert.equal(only(state).outcome, "stopped");
  assert.equal(only(state).problem, undefined);
});

test("ended without done: cut off, retryable, old history and signature kept", () => {
  let state = answer(INITIAL_CHAT_STATE, "t1", eventsOf("basic"));
  const { history, signature, questionsLeft } = state;
  state = answer(state, "t2", eventsOf("no-done"));
  state = chatReducer(state, { type: "ended", id: "t2" });
  const turn = state.turns[1];
  assert.equal(turn.phase, "done");
  assert.equal(turn.outcome, "interrupted");
  assert.deepEqual(turn.problem, { code: "interrupted", message: COPY.cutOff, retryable: true });
  assert.deepEqual(texts(turn.parts), ["Checking."]); // the partial text stays
  assert.equal(state.history, history);
  assert.equal(state.signature, signature);
  assert.equal(state.questionsLeft, questionsLeft);
});

test("stopped: marked stopped, the question isn't counted, and late events are ignored", () => {
  let state = answer(INITIAL_CHAT_STATE, "t1", [{ type: "text", delta: "Half" }]);
  state = chatReducer(state, { type: "stopped", id: "t1" });
  assert.equal(only(state).outcome, "stopped");
  assert.equal(only(state).phase, "done");
  assert.equal(only(state).problem, undefined);
  const late = run(state, [
    { type: "event", id: "t1", event: { type: "text", delta: " more" }, at: 9 },
    { type: "event", id: "t1", event: done(), at: 9 },
    { type: "ended", id: "t1" },
    { type: "stopped", id: "t1" },
  ]);
  assert.equal(late, state);
  assert.deepEqual(state.history, []);
  assert.equal(state.signature, null);
  assert.equal(state.questionsLeft, null);
});

test("ask is refused while a question is in flight, and for a repeated id", () => {
  const state = chatReducer(INITIAL_CHAT_STATE, { type: "ask", id: "t1", question: "Q?", at: 0 });
  assert.equal(chatReducer(state, { type: "ask", id: "t2", question: "R?", at: 0 }), state);
  const finished = chatReducer(state, { type: "stopped", id: "t1" });
  assert.equal(chatReducer(finished, { type: "ask", id: "t1", question: "R?", at: 0 }), finished);
});

test("ask with replaces swaps the failed last turn for a new one", () => {
  let state = answer(INITIAL_CHAT_STATE, "t1", eventsOf("basic"));
  state = answer(state, "t2", [{ type: "error", code: "busy", message: "busy" }, done()]);
  const first = state.turns[0];
  state = chatReducer(state, { type: "ask", id: "t3", question: "Q again", at: 0, replaces: "t2" });
  assert.deepEqual(state.turns.map((t) => t.id), ["t1", "t3"]);
  assert.equal(state.turns[0], first);
  // Only the last turn can be replaced.
  state = chatReducer(state, { type: "stopped", id: "t3" });
  state = chatReducer(state, { type: "ask", id: "t4", question: "Q", at: 0, replaces: "t1" });
  assert.deepEqual(state.turns.map((t) => t.id), ["t1", "t3", "t4"]);
});

test("ask with replaces keeps a failed turn the server counted, and adds the new one after it", () => {
  // error-timeout: a completed tool step, then a timeout; done brings back a longer history.
  let state = answer(INITIAL_CHAT_STATE, "t1", eventsOf("error-timeout"));
  assert.equal(only(state).counted, true);
  assert.equal(only(state).problem?.retryable, true);
  state = chatReducer(state, { type: "ask", id: "t2", question: "Q?", at: 0, replaces: "t1" });
  assert.deepEqual(state.turns.map((t) => t.id), ["t1", "t2"]);
  // A turn whose done brought the history back unchanged didn't count, so it is replaced.
  let other = answer(INITIAL_CHAT_STATE, "u1", [
    { type: "error", code: "busy", message: "busy" },
    done({ history: [], questions_used: 0, questions_left: 8 }),
  ]);
  assert.equal(only(other).counted, false);
  other = chatReducer(other, { type: "ask", id: "u2", question: "Q?", at: 0, replaces: "u1" });
  assert.deepEqual(other.turns.map((t) => t.id), ["u2"]);
});

test("restore marks unfinished turns as cut off and loading attachments as failed", () => {
  let saved = answer(INITIAL_CHAT_STATE, "t1", eventsOf("basic"));
  saved = chatReducer(saved, { type: "attach", turnId: "t1", forToolId: "toolu_basic_0", id: "a1" });
  saved = answer(saved, "t2", [{ type: "text", delta: "Half" }]);
  const state = chatReducer(INITIAL_CHAT_STATE, { type: "restore", state: saved });
  assert.equal(state.turns[1].outcome, "interrupted");
  assert.equal(state.turns[1].phase, "done");
  assert.equal(state.turns[1].problem?.code, "interrupted");
  assert.deepEqual(attachmentsFor(state.turns[0], "toolu_basic_0"), [
    { kind: "attachment", id: "a1", forToolId: "toolu_basic_0", state: "error", error: COPY.attachmentLost },
  ]);
  assert.equal(state.history, saved.history);
  assert.equal(canAsk(state), true);
  // A state with nothing to fix comes back as it was.
  const clean = answer(INITIAL_CHAT_STATE, "t1", eventsOf("basic"));
  assert.equal(chatReducer(INITIAL_CHAT_STATE, { type: "restore", state: clean }), clean);
});

test("restore drops a chat-off block but keeps the conversation's own blocks", () => {
  let off = chatReducer(INITIAL_CHAT_STATE, { type: "ask", id: "t1", question: "Q?", at: 0 });
  off = chatReducer(off, { type: "failed", id: "t1", error: { status: 503, code: "chat_disabled", message: "The chat is off." } });
  assert.equal(off.blocked?.code, "chat_disabled");
  const restored = chatReducer(INITIAL_CHAT_STATE, { type: "restore", state: off });
  assert.equal(restored.blocked, null);
  assert.equal(canAsk(restored), true);
  assert.equal(only(restored).problem?.code, "chat_disabled"); // the turn still says what happened
  const limit = answer(INITIAL_CHAT_STATE, "t8", [done({ questions_left: 0 })]);
  assert.equal(chatReducer(INITIAL_CHAT_STATE, { type: "restore", state: limit }), limit);
});

test("attachments leave the history, signature and questions left alone", () => {
  const base = answer(INITIAL_CHAT_STATE, "t1", eventsOf("basic"));
  let state = chatReducer(base, { type: "attach", turnId: "t1", forToolId: "toolu_basic_0", id: "a1" });
  assert.deepEqual(attachmentsFor(state.turns[0], "toolu_basic_0"), [
    { kind: "attachment", id: "a1", forToolId: "toolu_basic_0", state: "loading" },
  ]);
  const corner = { driver: "AAA", lap_number: 9, turn: "3" };
  state = chatReducer(state, { type: "attached", turnId: "t1", id: "a1", data: corner, summary: "Lap 9, turn 3" });
  assert.deepEqual(attachmentsFor(state.turns[0], "toolu_basic_0"), [
    { kind: "attachment", id: "a1", forToolId: "toolu_basic_0", state: "ok", data: corner, summary: "Lap 9, turn 3" },
  ]);
  state = chatReducer(state, { type: "attach", turnId: "t1", forToolId: "toolu_basic_0", id: "a2" });
  state = chatReducer(state, { type: "attached", turnId: "t1", id: "a2", error: "Can't reach the analysis server." });
  assert.equal(attachmentsFor(state.turns[0], "toolu_basic_0")[1].state, "error");
  assert.equal(state.history, base.history);
  assert.equal(state.signature, base.signature);
  assert.equal(state.questionsLeft, base.questionsLeft);
  assert.equal(state.blocked, base.blocked);
  // Unknown turns, tools and attachment ids change nothing.
  assert.equal(chatReducer(state, { type: "attach", turnId: "nope", forToolId: "toolu_basic_0", id: "a3" }), state);
  assert.equal(chatReducer(state, { type: "attach", turnId: "t1", forToolId: "nope", id: "a3" }), state);
  assert.equal(chatReducer(state, { type: "attach", turnId: "t1", forToolId: "toolu_basic_0", id: "a1" }), state);
  assert.equal(chatReducer(state, { type: "attached", turnId: "t1", id: "nope", data: 1 }), state);
});

test("an attachment keeps the corner it was opened for, through loading, the answer and a reload", () => {
  const base = answer(INITIAL_CHAT_STATE, "t1", eventsOf("basic"));
  const corner = { driver: "AAA", lap_number: 9, turn: "3" };
  let state = chatReducer(base, { type: "attach", turnId: "t1", forToolId: "toolu_basic_0", id: "a1", corner });
  assert.deepEqual(attachmentsFor(state.turns[0], "toolu_basic_0")[0].corner, corner);
  const loading = state;
  state = chatReducer(state, { type: "attached", turnId: "t1", id: "a1", data: { x: 1 }, summary: "S" });
  assert.deepEqual(attachmentsFor(state.turns[0], "toolu_basic_0")[0].corner, corner);
  // Saved while it loaded: restored as failed, still naming its corner.
  const restored = chatReducer(INITIAL_CHAT_STATE, { type: "restore", state: loading });
  const part = attachmentsFor(restored.turns[0], "toolu_basic_0")[0];
  assert.deepEqual([part.state, part.error, part.corner], ["error", COPY.attachmentLost, corner]);
});

test("attachments work in a blocked conversation and stay after the streamed parts", () => {
  let state = answer(INITIAL_CHAT_STATE, "t1", [
    { type: "tool_call", id: "a", name: "find_mistakes", input: {} },
    { type: "tool_result", id: "a", name: "find_mistakes", is_error: false, summary: "s", chart: { bundle: "find-mistakes", resource_uri: "", data: {} } },
  ]);
  state = chatReducer(state, { type: "attach", turnId: "t1", forToolId: "a", id: "x" });
  state = run(state, [
    { type: "event", id: "t1", event: { type: "text", delta: "After" }, at: 0 },
    { type: "event", id: "t1", event: { type: "text", delta: " the chart." }, at: 0 },
    { type: "event", id: "t1", event: { type: "retry", message: "" }, at: 0 },
    { type: "event", id: "t1", event: { type: "text", delta: "Again." }, at: 0 },
    { type: "event", id: "t1", event: done({ questions_left: 0 }), at: 0 },
  ]);
  assert.deepEqual(kinds(only(state).parts), ["tool:a", "text", "attachment"]);
  assert.deepEqual(texts(only(state).parts), ["Again."]);
  assert.ok(state.blocked);
  state = chatReducer(state, { type: "attached", turnId: "t1", id: "x", data: { ok: true } });
  assert.equal(attachmentsFor(only(state), "a")[0].state, "ok");
});

test("the chart object and the tool part keep their identity across 100 text deltas", () => {
  let state = answer(INITIAL_CHAT_STATE, "t1", eventsOf("basic").slice(0, 4)); // up to tool_result
  const tool = only(state).parts[1] as ToolPart;
  const chart = tool.result?.chart;
  assert.ok(chart);
  const otherTurns = state.turns;
  for (let i = 0; i < 100; i++) {
    state = chatReducer(state, { type: "event", id: "t1", event: { type: "text", delta: `${i} ` }, at: i });
    const now = only(state).parts[1] as ToolPart;
    assert.equal(now, tool);
    assert.equal(now.result?.chart, chart);
  }
  assert.notEqual(state.turns, otherTurns);
  assert.equal(texts(only(state).parts)[1].split(" ").length, 101);
});

test("earlier turns keep their identity while a new one streams", () => {
  let state = answer(INITIAL_CHAT_STATE, "t1", eventsOf("basic"));
  const first = state.turns[0];
  state = answer(state, "t2", eventsOf("retry"));
  assert.equal(state.turns[0], first);
});

test("replay(events) equals reducing them live", () => {
  for (const name of ["basic", "retry", "refusal", "error-timeout", "no-done"]) {
    const events = eventsOf(name);
    let live = chatReducer(INITIAL_CHAT_STATE, { type: "ask", id: "replay", question: "Q?", at: 0 });
    for (const event of events) live = chatReducer(live, { type: "event", id: "replay", event, at: 0 });
    live = chatReducer(live, { type: "ended", id: "replay" }); // what the store does when the stream closes
    assert.deepEqual(replay(events, "Q?"), live.turns[0], name);
  }
  assert.equal(replay(eventsOf("no-done"), "Q?").outcome, "interrupted");
  assert.equal(replay(eventsOf("basic"), "Q?").outcome, "answered");
});

test("reset starts over, even when blocked", () => {
  const blocked = answer(INITIAL_CHAT_STATE, "t1", [done({ questions_left: 0 })]);
  assert.equal(chatReducer(blocked, { type: "reset" }), INITIAL_CHAT_STATE);
});


// ---- M7: the limits and saved answers (plan 4.4) ----

const MIN = 60_000;

const status = (over: Partial<ChatStatus> = {}): ChatStatus => ({
  available: true,
  mode: "anthropic",
  reason: null,
  retry_after_s: null,
  quota: { hour_left: 8, day_left: 21, per_hour: 10, per_day: 25 },
  visitor: "3fa2c1",
  saved: [],
  ...over,
});

/** A conversation with one answer, then question `id` refused before its stream. */
function refused(code: string, extra: { retryAfterS?: number | null; limit?: string } = {}, at = 1_000_000) {
  // (basic's done has no quota, as from a server without limits: the status comes after it.)
  const before = chatReducer(answer(INITIAL_CHAT_STATE, "t1", eventsOf("basic")), { type: "chatStatus", status: status(), at: 0 });
  let state = chatReducer(before, { type: "ask", id: "t2", question: "Summarise the 2023 Monaco Grand Prix.", at });
  state = chatReducer(state, { type: "failed", id: "t2", error: { status: code === "rate_limited" ? 429 : 503, code, message: "server words", ...extra }, at });
  return { before, state };
}

test("each limit's refusal sets unavailable, takes the refused turn out and hands back its question", () => {
  const cases: [string, { retryAfterS?: number | null; limit?: string }, Unavailable][] = [
    ["rate_limited", { retryAfterS: 1380, limit: "hour" }, { code: "rate_limited", message: "server words", retryAt: 1_000_000 + 1380_000, limit: "hour", max: 10 }],
    ["rate_limited", { retryAfterS: 4000, limit: "day" }, { code: "rate_limited", message: "server words", retryAt: 1_000_000 + 4000_000, limit: "day", max: 25 }],
    ["rate_limited", { retryAfterS: 30 }, { code: "rate_limited", message: "server words", retryAt: 1_000_000 + 30_000 }],
    ["daily_cap", { retryAfterS: 7200 }, { code: "daily_cap", message: "server words", retryAt: 1_000_000 + 7200_000 }],
    ["chat_paused", { retryAfterS: null }, { code: "chat_paused", message: "server words", retryAt: null }],
    ["chat_unavailable", { retryAfterS: 60 }, { code: "chat_unavailable", message: "server words", retryAt: 1_000_000 + 60_000 }],
  ];
  for (const [code, extra, expected] of cases) {
    assert.ok(LIMIT_CODES.has(code));
    const { before, state } = refused(code, extra);
    assert.deepEqual(state.unavailable, { ...expected, question: "Summarise the 2023 Monaco Grand Prix." }, code);
    // Nothing was counted: the transcript, the history, the signature and the meter are as before.
    assert.deepEqual(state.turns, before.turns, code);
    assert.equal(state.history, before.history, code);
    assert.equal(state.signature, before.signature, code);
    assert.equal(state.questionsLeft, before.questionsLeft, code);
    assert.equal(state.blocked, null, code);
    assert.equal(canAsk(state), false, code);
  }
  // Without the time it was refused at, there is no retryAt; a late refusal of a finished turn is ignored.
  let state = chatReducer(INITIAL_CHAT_STATE, { type: "ask", id: "t", question: "Q?", at: 0 });
  state = chatReducer(state, { type: "failed", id: "t", error: { status: 429, code: "rate_limited", message: "m", retryAfterS: 5, limit: "hour" } });
  assert.equal(state.unavailable?.retryAt, null);
  assert.equal(state.unavailable?.max, undefined); // no status said how many
  const done1 = answer(INITIAL_CHAT_STATE, "t1", eventsOf("basic"));
  assert.equal(chatReducer(done1, { type: "failed", id: "t1", error: { status: 503, code: "daily_cap", message: "m" }, at: 1 }), done1);
});

test("chat_disabled keeps M6's block and its turn, and sets unavailable too", () => {
  let state = chatReducer(INITIAL_CHAT_STATE, { type: "ask", id: "t1", question: "Q?", at: 0 });
  state = chatReducer(state, { type: "failed", id: "t1", error: { status: 503, code: "chat_disabled", message: "The chat is off." }, at: 5 });
  assert.equal(state.blocked?.code, "chat_disabled");
  assert.equal(only(state).problem?.code, "chat_disabled");
  assert.deepEqual(state.unavailable, { code: "chat_disabled", message: "The chat is off.", retryAt: null });
  // After a reload the server is asked again: neither the block nor unavailable is kept.
  const restored = chatReducer(INITIAL_CHAT_STATE, { type: "restore", state });
  assert.equal(restored.blocked, null);
  assert.equal(restored.unavailable, null);
});

test("the status sets unavailable and the quota before anyone types; an available chat clears it", () => {
  const at = 2_000_000;
  const cases: [Partial<ChatStatus>, Unavailable | null][] = [
    [{}, null],
    [{ available: false, reason: "paused", mode: "anthropic" }, { code: "chat_paused", message: "", retryAt: null }],
    [{ available: false, reason: "off", mode: "off", quota: null }, { code: "chat_disabled", message: "", retryAt: null }],
    [{ available: false, reason: "daily_cap", retry_after_s: 3600 }, { code: "daily_cap", message: "", retryAt: at + 3600_000 }],
    [{ available: false, reason: "unavailable", retry_after_s: 60, quota: null }, { code: "chat_unavailable", message: "", retryAt: at + 60_000 }],
    [
      { available: false, reason: "rate_limited", retry_after_s: 600, quota: { hour_left: 0, day_left: 12, per_hour: 3, per_day: 25 } },
      { code: "rate_limited", message: "", retryAt: at + 600_000, limit: "hour", max: 3 },
    ],
    [
      { available: false, reason: "rate_limited", retry_after_s: 9000, quota: { hour_left: 4, day_left: 0, per_hour: 10, per_day: 25 } },
      { code: "rate_limited", message: "", retryAt: at + 9000_000, limit: "day", max: 25 },
    ],
  ];
  for (const [over, expected] of cases) {
    const s = status(over);
    const state = chatReducer(INITIAL_CHAT_STATE, { type: "chatStatus", status: s, at });
    assert.deepEqual(state.unavailable, expected, JSON.stringify(over));
    assert.deepEqual(state.quota, s.quota, JSON.stringify(over));
    assert.equal(canAsk(state), expected === null);
  }
  const closed = chatReducer(INITIAL_CHAT_STATE, { type: "chatStatus", status: status({ available: false, reason: "paused" }), at });
  assert.equal(chatReducer(closed, { type: "chatStatus", status: status(), at }).unavailable, null);
});

test("tick clears unavailable once retryAt has passed, never before, and never without one", () => {
  const { state } = refused("rate_limited", { retryAfterS: 600, limit: "hour" }, 0);
  assert.equal(chatReducer(state, { type: "tick", at: 600_000 - 1 }), state);
  const open = chatReducer(state, { type: "tick", at: 600_000 });
  assert.equal(open.unavailable, null);
  assert.equal(canAsk(open), true);
  const paused = refused("chat_paused", { retryAfterS: null }).state;
  assert.equal(chatReducer(paused, { type: "tick", at: Number.MAX_SAFE_INTEGER }), paused);
  assert.equal(chatReducer(INITIAL_CHAT_STATE, { type: "tick", at: 1 }), INITIAL_CHAT_STATE);
});

test("done keeps the visitor's quota with the status's sizes; no quota means the limits are off", () => {
  let state = chatReducer(INITIAL_CHAT_STATE, { type: "chatStatus", status: status(), at: 0 });
  state = answer(state, "t1", [done({ quota: { hour_left: 7, day_left: 20 } })]);
  assert.deepEqual(state.quota, { hour_left: 7, day_left: 20, per_hour: 10, per_day: 25 });
  state = answer(state, "t2", [done({ quota: null })]);
  assert.equal(state.quota, null);
  assert.deepEqual(answer(INITIAL_CHAT_STATE, "t1", [done({ quota: { hour_left: 1, day_left: 2 } })]).quota, { hour_left: 1, day_left: 2 });
});

test("showSaved adds the recording as a marked turn, and never touches the history, signature or meter", () => {
  const saved = parseSavedAnswer(savedFixture("saved-basic"));
  assert.ok(saved);
  const base = answer(INITIAL_CHAT_STATE, "t1", eventsOf("basic"), "Asked live");
  const show = { type: "showSaved", id: saved.id, question: saved.question, events: saved.events, recordedAt: saved.recorded_at } as const;
  const state = chatReducer(base, { ...show, turnId: "s1" });
  assert.equal(state.turns.length, 2);
  const turn = state.turns[1];
  assert.equal(turn.id, "s1");
  assert.deepEqual(turn.saved, { id: "last-race-mistakes", recordedAt: "2026-10-04T13:05:12Z" });
  assert.equal(turn.question, "Who made the biggest mistakes in the last race?");
  assert.equal(turn.phase, "done");
  assert.equal(turn.outcome, "answered");
  assert.equal(turn.counted, false);
  assert.equal(chartParts(turn).length, 1);
  assert.equal(state.history, base.history);
  assert.equal(state.signature, base.signature);
  assert.equal(state.questionsLeft, base.questionsLeft);
  assert.equal(state.turns[0], base.turns[0]);
  // Folded exactly as reducing the same events live would.
  const { saved: mark, ...rest } = turn;
  assert.ok(mark);
  assert.deepEqual(rest, replay(saved.events, saved.question, "s1"));
  // Once per recording; the turn id is derived when none is given.
  assert.equal(chatReducer(state, { ...show, turnId: "s2" }), state);
  assert.equal(chatReducer(INITIAL_CHAT_STATE, show).turns[0].id, "saved-last-race-mistakes");
});

test("saved answers can be shown while the chat is unavailable or the conversation blocked, not while streaming", () => {
  const saved = parseSavedAnswer(savedFixture("saved-basic"));
  assert.ok(saved);
  const show = { type: "showSaved", id: saved.id, question: saved.question, events: saved.events, recordedAt: saved.recorded_at, turnId: "s" } as const;
  const { state: limited } = refused("rate_limited", { retryAfterS: 60, limit: "hour" });
  const shown = chatReducer(limited, show);
  assert.equal(shown.turns.length, limited.turns.length + 1);
  assert.equal(shown.unavailable, limited.unavailable);
  const blocked = answer(INITIAL_CHAT_STATE, "t8", [done({ questions_left: 0 })]);
  assert.equal(chatReducer(blocked, show).turns.length, 2);
  const streaming = answer(INITIAL_CHAT_STATE, "t1", [{ type: "text", delta: "Half" }]);
  assert.equal(chatReducer(streaming, show), streaming);
});

test("restore drops the limits (the page asks again), keeps saved turns, and drops a malformed mark", () => {
  const saved = parseSavedAnswer(savedFixture("saved-basic"));
  assert.ok(saved);
  const { state: limited } = refused("rate_limited", { retryAfterS: 60, limit: "hour" });
  const withSaved = chatReducer(limited, { type: "showSaved", id: saved.id, question: saved.question, events: saved.events, recordedAt: saved.recorded_at, turnId: "s" });
  const restored = chatReducer(INITIAL_CHAT_STATE, { type: "restore", state: withSaved });
  assert.equal(restored.unavailable, null);
  assert.equal(restored.quota, null);
  assert.deepEqual(restored.turns, withSaved.turns);
  assert.equal(canAsk(restored), true);
  const broken = { ...withSaved, turns: withSaved.turns.map((t) => (t.id === "s" ? { ...t, saved: { id: 3 } as never } : t)) };
  const fixed = chatReducer(INITIAL_CHAT_STATE, { type: "restore", state: broken });
  assert.equal(fixed.turns.find((t) => t.id === "s")?.saved, undefined);
  // A copy saved before M7 has neither field, and comes back with both.
  const old = { turns: [], history: [], signature: null, questionsLeft: null, blocked: null } as unknown as ChatState;
  const upgraded = chatReducer(INITIAL_CHAT_STATE, { type: "restore", state: old });
  assert.equal(upgraded.unavailable, null);
  assert.equal(upgraded.quota, null);
});

test("reset starts a new conversation but keeps the limits, which are the visitor's", () => {
  const { state } = refused("daily_cap", { retryAfterS: 60 });
  const fresh = chatReducer(state, { type: "reset" });
  assert.deepEqual(fresh.turns, []);
  assert.equal(fresh.history.length, 0);
  assert.equal(fresh.unavailable, state.unavailable);
  assert.equal(fresh.quota, state.quota);
});

test("the notice's words for each code (plan 4.4), with the wait counted from retryAt", () => {
  const now = 1_000_000;
  const copy = (u: Omit<Unavailable, "message">, at = now) => unavailableCopy({ message: "server words", ...u }, at);
  assert.deepEqual(copy({ code: "rate_limited", limit: "hour", max: 10, retryAt: now + 23 * MIN }), {
    tone: "warn",
    title: "You've asked 10 questions in the last hour from this connection. You can ask again in 23 min.",
    spoken: `You've asked 10 questions in the last hour from this connection. You can ask again at ${clockTime(now + 23 * MIN)}.`,
    saved: "Meanwhile, saved answers show what the chat does.",
    meter: "Ask again in 23 min",
  });
  // Rounded up, never "0 min"; a smaller limit says its own size; no size, no number.
  assert.equal(copy({ code: "rate_limited", limit: "hour", max: 3, retryAt: now + 1000 }).meter, "Ask again in 1 min");
  assert.match(copy({ code: "rate_limited", limit: "hour", max: 3, retryAt: now + 61_000 }).title, /^You've asked 3 questions .* in 2 min\.$/);
  assert.equal(
    copy({ code: "rate_limited", limit: "hour", retryAt: null }).title,
    "You've reached this connection's hourly limit of questions. You can ask again later.",
  );
  assert.deepEqual(copy({ code: "rate_limited", limit: "day", max: 25, retryAt: now + (3 * 60 + 12) * MIN }), {
    tone: "warn",
    title: "That's today's 25 questions from this connection. The count resets at midnight UTC, in 3 h 12 min.",
    spoken: "That's today's 25 questions from this connection. The count resets at midnight UTC.",
    saved: "Meanwhile, saved answers show what the chat does.",
    meter: "Ask again in 3 h 12 min",
  });
  assert.equal(copy({ code: "rate_limited", limit: "day", max: 25, retryAt: now + 12 * MIN }).meter, "Ask again in 12 min");
  assert.equal(copy({ code: "rate_limited", limit: "day", max: 25, retryAt: now + 120 * MIN }).meter, "Ask again in 2 h");
  assert.equal(copy({ code: "rate_limited", retryAt: now + 20_000 }).title, "Too many questions from this connection at once. You can ask again in 20 s.");
  assert.deepEqual(copy({ code: "daily_cap", retryAt: now + 5 * MIN }), {
    tone: "info",
    title: "The chat has used today's budget for this free demo. It resets at midnight UTC.",
    spoken: "The chat has used today's budget for this free demo. It resets at midnight UTC.",
    saved: "Here are answers it gave earlier.",
    meter: "Resets at midnight UTC",
  });
  assert.deepEqual(copy({ code: "chat_paused", retryAt: null }), {
    tone: "info",
    title: "The chat is paused for now.",
    spoken: "The chat is paused for now.",
    saved: "Here are answers it gave earlier.",
    meter: "Chat paused",
  });
  assert.deepEqual(copy({ code: "chat_unavailable", retryAt: now + MIN }), {
    tone: "warn",
    title: "The chat can't check its limits right now, so it's off for a moment.",
    spoken: "The chat can't check its limits right now, so it's off for a moment.",
    saved: "Here are answers it gave earlier.",
    meter: "Chat off for a moment",
  });
  assert.equal(copy({ code: "chat_disabled", retryAt: null }).title, COPY.chatOff);
});

test("the notice's spoken words hold while it counts down (it is a live region)", () => {
  const now = 1_000_000;
  const retryAt = now + 23 * MIN;
  const u: Unavailable = { code: "rate_limited", message: "", retryAt, limit: "hour", max: 10 };
  const early = unavailableCopy(u, now);
  const later = unavailableCopy(u, now + 7 * MIN);
  assert.notEqual(early.title, later.title); // in 23 min, then in 16 min
  assert.equal(early.spoken, later.spoken);
  assert.ok(early.spoken.endsWith(`You can ask again at ${clockTime(retryAt)}.`));
  const day: Unavailable = { code: "rate_limited", message: "", retryAt, limit: "day", max: 25 };
  assert.equal(unavailableCopy(day, now).spoken, unavailableCopy(day, now + MIN).spoken);
  const rate: Unavailable = { code: "rate_limited", message: "", retryAt: now + 20_000 };
  assert.equal(unavailableCopy(rate, now).spoken, "Too many questions from this connection at once. Try again shortly.");
  // Without a countdown the two are the same, so the page shows the title alone.
  const later2 = { code: "rate_limited", message: "", retryAt: null, limit: "hour" } as const;
  assert.equal(unavailableCopy(later2, now).spoken, unavailableCopy(later2, now).title);
  assert.match(clockTime(Date.UTC(2026, 9, 4, 14, 5)), /\d{1,2}[:.]05/);
});

test("a saved turn's line says why it shows, while that holds", () => {
  const note = (u: Omit<Unavailable, "message" | "retryAt"> | null) => savedNote(u === null ? null : { message: "", retryAt: null, ...u });
  assert.equal(note(null), "Recorded earlier with the same tools.");
  assert.equal(note({ code: "chat_paused" }), "Recorded earlier with the same tools, while the live chat is paused.");
  assert.equal(note({ code: "daily_cap" }), "Recorded earlier with the same tools, while the live chat is at today's limit.");
  assert.equal(note({ code: "rate_limited", limit: "hour" }), "Recorded earlier with the same tools, while the live chat is at your hourly limit.");
  assert.equal(note({ code: "rate_limited", limit: "day" }), "Recorded earlier with the same tools, while the live chat is at your daily limit.");
  assert.equal(note({ code: "chat_disabled" }), "Recorded earlier with the same tools, while the live chat is off.");
  assert.equal(note({ code: "chat_unavailable" }), "Recorded earlier with the same tools, while the live chat is off for a moment.");
});

test("the daily meter shows from 5 left, unless the conversation's own limit comes first", () => {
  const q = (day_left: number) => ({ hour_left: 9, day_left });
  assert.equal(dailyMeter(null, 7), null);
  assert.equal(dailyMeter(q(6), 7), null);
  assert.equal(dailyMeter(q(5), 7), "5 questions left today");
  assert.equal(dailyMeter(q(1), null), "1 question left today");
  assert.equal(dailyMeter(q(0), 7), "No questions left today");
  assert.equal(dailyMeter(q(4), 3), null); // "3 questions left in this conversation" says more
  assert.equal(dailyMeter(q(3), 3), "3 questions left today");
});
