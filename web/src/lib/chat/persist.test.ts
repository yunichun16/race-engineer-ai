import assert from "node:assert/strict";
import { test } from "node:test";

import { explorerLink } from "./links.ts";
import {
  PENDING_KEY,
  STORAGE_KEY,
  loadChat,
  saveChat,
  savePending,
  sessionStorageOrNull,
  takePending,
  withoutChartData,
} from "./persist.ts";
import { INITIAL_CHAT_STATE, chatReducer, type ChatAction, type ChatState, type ToolPart } from "./reducer.ts";
import { createSSEDecoder, parseChatEvent } from "./sse.ts";
import { fixtureBytes, memoryStorage } from "./test-support.ts";
import type { ChatEvent } from "./types.ts";

function eventsOf(name: string): ChatEvent[] {
  const decoder = createSSEDecoder();
  return [...decoder.feed(fixtureBytes(name)), ...decoder.end()]
    .map((m) => parseChatEvent(m.event, m.data))
    .filter((e): e is ChatEvent => e !== null);
}

/** One answered question with a find-mistakes chart and a loaded corner attachment. */
function conversation(): ChatState {
  const actions: ChatAction[] = [{ type: "ask", id: "t1", question: "Q?", at: 0 }];
  for (const event of eventsOf("basic")) actions.push({ type: "event", id: "t1", event, at: 1 });
  actions.push({
    type: "attach",
    turnId: "t1",
    forToolId: "toolu_basic_0",
    id: "a1",
    corner: { driver: "AAA", lap_number: 9, turn: "3" },
  });
  actions.push({
    type: "attached",
    turnId: "t1",
    id: "a1",
    summary: "Lap 9, turn 3",
    data: { year: 2026, event: "Sample Grand Prix", session_code: "Q", driver: "AAA", lap_number: 9, turn: "3", lap: { speed: new Array(80).fill(250) } },
  });
  return actions.reduce(chatReducer, INITIAL_CHAT_STATE);
}

test("a saved conversation loads back as it was", () => {
  const storage = memoryStorage();
  const state = conversation();
  assert.equal(saveChat(storage, state), "saved");
  assert.deepEqual(JSON.parse(storage.map.get(STORAGE_KEY) ?? "").v, 1);
  assert.deepEqual(loadChat(storage), state);
  assert.equal(saveChat(storage, INITIAL_CHAT_STATE), "saved");
  assert.deepEqual(loadChat(storage), INITIAL_CHAT_STATE);
});

test("nothing saved, broken JSON, another version or a wrong shape loads as nothing", () => {
  const storage = memoryStorage();
  assert.equal(loadChat(storage), null);
  assert.equal(loadChat(null), null);
  const good = JSON.parse(JSON.stringify({ v: 1, state: conversation() }));
  const bad: unknown[] = [
    "{not json",
    { v: 2, state: good.state },
    { v: 1, state: { ...good.state, turns: "x" } },
    { v: 1, state: { ...good.state, signature: 3 } },
    { v: 1, state: { ...good.state, blocked: { code: "turn_limit" } } },
    { v: 1, state: { ...good.state, turns: [{ ...good.state.turns[0], phase: "thinking" }] } },
    { v: 1, state: { ...good.state, turns: [{ ...good.state.turns[0], id: 1 }] } },
    { v: 1, state: { ...good.state, turns: [{ ...good.state.turns[0], parts: [{ kind: "html", html: "<b>" }] }] } },
    { v: 1, state: { ...good.state, turns: [{ ...good.state.turns[0], problem: { code: "x" } }] } },
    { v: 1, state: { ...good.state, turns: [{ ...good.state.turns[0], counted: "yes" }] } },
    // Fields a card shows as text must be text.
    {
      v: 1,
      state: {
        ...good.state,
        turns: [{ ...good.state.turns[0], parts: [{ kind: "attachment", id: "a", forToolId: "t", state: "ok", summary: { a: 1 } }] }],
      },
    },
    {
      v: 1,
      state: {
        ...good.state,
        turns: [{ ...good.state.turns[0], parts: [{ kind: "attachment", id: "a", forToolId: "t", state: "error", error: 404 }] }],
      },
    },
    {
      v: 1,
      state: {
        ...good.state,
        turns: [
          {
            ...good.state.turns[0],
            parts: [{ kind: "attachment", id: "a", forToolId: "t", state: "loading", corner: { driver: "AAA", lap_number: "9", turn: "3" } }],
          },
        ],
      },
    },
  ];
  for (const value of bad) {
    storage.map.set(STORAGE_KEY, typeof value === "string" ? value : JSON.stringify(value));
    assert.equal(loadChat(storage), null, JSON.stringify(value).slice(0, 80));
  }
  storage.map.set(STORAGE_KEY, JSON.stringify(good));
  assert.ok(loadChat(storage));
});

test("there is no default storage outside a browser, even where Node has a sessionStorage global", () => {
  // Node 25 and later define sessionStorage for the whole process; a server render must not use it.
  assert.equal(typeof window, "undefined");
  assert.equal(sessionStorageOrNull(), null);
});

test("storage that throws neither breaks saving nor loading", () => {
  const broken = {
    getItem(): string | null {
      throw new DOMException("blocked", "SecurityError");
    },
    setItem(): void {
      throw new DOMException("blocked", "SecurityError");
    },
    removeItem(): void {
      throw new DOMException("blocked", "SecurityError");
    },
  };
  assert.equal(saveChat(broken, conversation()), "failed");
  assert.equal(loadChat(broken), null);
  assert.equal(saveChat(null, conversation()), "failed");
});

test("on a quota error the charts' data is cut to their link fields and marked dropped", () => {
  const state = conversation();
  const full = JSON.stringify({ v: 1, state }).length;
  const storage = memoryStorage(full - 1); // the whole state doesn't fit, the lighter one does
  assert.equal(saveChat(storage, state), "saved-without-charts");
  const loaded = loadChat(storage);
  assert.ok(loaded);
  const tool = loaded.turns[0].parts.find((p) => p.kind === "tool") as ToolPart;
  const chart = tool.result?.chart;
  assert.equal(chart?.dropped, true);
  assert.deepEqual(chart?.data, { year: 2026, event: "Sample Grand Prix", session: "Qualifying", session_code: "Q" });
  // The card can still link to the explorer.
  const original = (state.turns[0].parts.find((p) => p.kind === "tool") as ToolPart).result?.chart;
  assert.deepEqual(explorerLink(chart), explorerLink(original));
  const attachment = loaded.turns[0].parts.find((p) => p.kind === "attachment");
  assert.ok(attachment?.kind === "attachment");
  assert.equal(attachment.dropped, true);
  assert.equal(attachment.summary, "Lap 9, turn 3");
  assert.deepEqual(attachment.data, { year: 2026, event: "Sample Grand Prix", session_code: "Q", driver: "AAA", lap_number: 9, turn: "3" });
  // History, signature and text are kept whole: the conversation can carry on.
  assert.deepEqual(loaded.history, state.history);
  assert.equal(loaded.signature, state.signature);
  // The state in memory is untouched.
  assert.notEqual(original?.dropped, true);
});

test("when even the lighter copy doesn't fit, the old copy is removed", () => {
  const storage = memoryStorage(10);
  storage.map.set(STORAGE_KEY, "old conversation");
  assert.equal(saveChat(storage, conversation()), "failed");
  assert.equal(storage.map.has(STORAGE_KEY), false);
});

test("withoutChartData leaves dropped charts and text-only tools alone", () => {
  const once = withoutChartData(conversation());
  const twice = withoutChartData(once);
  assert.deepEqual(twice, once);
  assert.deepEqual(withoutChartData(INITIAL_CHAT_STATE), INITIAL_CHAT_STATE);
});

test("the pending question is read once and deleted", () => {
  const storage = memoryStorage();
  assert.equal(takePending(storage), null);
  assert.equal(savePending(storage, "  Summarise the 2023 Monaco Grand Prix.  "), true);
  assert.equal(storage.map.get(PENDING_KEY), "Summarise the 2023 Monaco Grand Prix.");
  assert.equal(takePending(storage), "Summarise the 2023 Monaco Grand Prix.");
  assert.equal(takePending(storage), null);
  assert.equal(storage.map.has(PENDING_KEY), false);
  storage.map.set(PENDING_KEY, "   ");
  assert.equal(takePending(storage), null);
  assert.equal(storage.map.has(PENDING_KEY), false);
  assert.equal(savePending(storage, " "), false);
  assert.equal(savePending(null, "Q"), false);
  storage.map.set(PENDING_KEY, "x".repeat(2500));
  assert.equal(takePending(storage)?.length, 2000);
});

test("a pending question that can't be deleted isn't sent", () => {
  const stuck = {
    getItem: (): string | null => "Who will win?",
    setItem: (): void => {},
    removeItem: (): void => {
      throw new DOMException("blocked", "SecurityError");
    },
  };
  assert.equal(takePending(stuck), null);
});
