import assert from "node:assert/strict";
import { test } from "node:test";

import {
  createSSEDecoder,
  isChatEventType,
  parseChatEvent,
  parseChatStream,
  parseSSE,
  type SSEMessage,
} from "./sse.ts";
import { bytes, collect, everyByte, fixtureBytes, splitAt, streamOf } from "./test-support.ts";

/** Runs the decoder over chunks without a stream around it. */
function decode(chunks: Uint8Array[]): SSEMessage[] {
  const decoder = createSSEDecoder();
  const out: SSEMessage[] = [];
  for (const chunk of chunks) out.push(...decoder.feed(chunk));
  out.push(...decoder.end());
  return out;
}

function decodeText(text: string): SSEMessage[] {
  return decode([bytes(text)]);
}

const FIXTURES = ["basic", "retry", "refusal", "error-timeout", "no-done"];

test("the basic fixture gives its events in order, without the keep-alive comment", () => {
  const events = decode([fixtureBytes("basic")]);
  assert.deepEqual(
    events.map((e) => e.event),
    ["status", "text", "tool_call", "tool_result", "text", "text", "text", "done"],
  );
  for (const e of events) assert.doesNotThrow(() => JSON.parse(e.data));
});

test("every fixture gives the same events when split at every byte offset", () => {
  for (const name of FIXTURES) {
    const data = fixtureBytes(name);
    const whole = decode([data]);
    assert.ok(whole.length > 0, name);
    for (let offset = 0; offset <= data.length; offset++) {
      assert.deepEqual(decode(splitAt(data, [offset])), whole, `${name} split at ${offset}`);
    }
  }
});

test("every fixture gives the same events fed one byte at a time", () => {
  for (const name of FIXTURES) {
    const data = fixtureBytes(name);
    assert.deepEqual(decode(everyByte(data)), decode([data]), name);
  }
});

test("CRLF and CR line ends give the same events, split at every byte offset", () => {
  const text = new TextDecoder().decode(fixtureBytes("basic"));
  const whole = decode([fixtureBytes("basic")]);
  for (const ending of ["\r\n", "\r"]) {
    const data = bytes(text.replaceAll("\n", ending));
    assert.deepEqual(decode([data]), whole, JSON.stringify(ending));
    for (let offset = 0; offset <= data.length; offset++) {
      assert.deepEqual(decode(splitAt(data, [offset])), whole, `${JSON.stringify(ending)} at ${offset}`);
    }
  }
});

test("a sample with a BOM, a comment, CRLF, CR and multi-byte text survives every pair of cuts", () => {
  const data = bytes('﻿: ping\r\n\r\nevent: text\r\ndata: {"delta": "café 🏁"}\r\rdata: a\ndata:b\n\n');
  const whole = decode([data]);
  assert.deepEqual(whole, [
    { event: "text", data: '{"delta": "café 🏁"}' },
    { event: "message", data: "a\nb" },
  ]);
  for (let i = 0; i <= data.length; i++) {
    for (let j = i; j <= data.length; j++) {
      assert.deepEqual(decode(splitAt(data, [i, j])), whole, `cut at ${i} and ${j}`);
    }
  }
});

test("a CR at the end of one chunk and LF at the start of the next are one line end", () => {
  const events = decode([bytes("event: a\r"), bytes("\ndata: 1\r"), bytes("\n\r"), bytes("\n")]);
  assert.deepEqual(events, [{ event: "a", data: "1" }]);
  // A lone CR followed by a chunk that doesn't start with LF still ends the line.
  assert.deepEqual(decode([bytes("data: x\r"), bytes("\r")]), [{ event: "message", data: "x" }]);
  // The CR flag survives an empty chunk in between.
  assert.deepEqual(decode([bytes("data: y\r"), new Uint8Array(0), bytes("\n\n")]), [
    { event: "message", data: "y" },
  ]);
});

test("é and 🏁 split across chunks decode whole", () => {
  const data = bytes('data: {"delta": "café 🏁"}\n\n');
  const e = data.indexOf(0xc3); // é is C3 A9
  const flag = data.indexOf(0xf0); // 🏁 is F0 9F 8F 81
  const cases = [[e + 1], [flag + 1], [flag + 2], [flag + 3], [e + 1, flag + 1, flag + 2, flag + 3]];
  for (const offsets of cases) {
    const [event] = decode(splitAt(data, offsets));
    assert.equal(JSON.parse(event.data).delta, "café 🏁", String(offsets));
  }
});

test("one leading byte order mark is dropped, also when split, and only one", () => {
  assert.deepEqual(decodeText("\uFEFFevent: a\ndata: 1\n\n"), [{ event: "a", data: "1" }]);
  const data = bytes("\uFEFFevent: a\ndata: 1\n\n");
  assert.deepEqual(decode(splitAt(data, [1, 2])), [{ event: "a", data: "1" }]);
  // A second mark is part of the first line's field name, which is then unknown.
  assert.deepEqual(decodeText("\uFEFF\uFEFFevent: a\ndata: 1\n\n"), [{ event: "message", data: "1" }]);
  // A mark later in the stream is data.
  assert.deepEqual(decodeText("data: \uFEFFx\n\n"), [{ event: "message", data: "\uFEFFx" }]);
});

test("fields: one space after the colon is removed, a line without a colon has no value", () => {
  assert.deepEqual(decodeText("data:tight\n\n"), [{ event: "message", data: "tight" }]);
  assert.deepEqual(decodeText("data:  two\n\n"), [{ event: "message", data: " two" }]);
  assert.deepEqual(decodeText("data\n\n"), [{ event: "message", data: "" }]);
  assert.deepEqual(decodeText("event\ndata: x\n\n"), [{ event: "message", data: "x" }]);
  assert.deepEqual(decodeText("data: a: b\n\n"), [{ event: "message", data: "a: b" }]);
});

test("several data lines join with LF; the last event line wins", () => {
  assert.deepEqual(decodeText("event: a\nevent: b\ndata: 1\ndata:\ndata: 2\n\n"), [
    { event: "b", data: "1\n\n2" },
  ]);
});

test("comments, id, retry and unknown fields are ignored", () => {
  const text = ": ping\n\n:\nid: 7\nretry: 1000\nfoo: bar\nevent: a\n: inside\ndata: 1\n\n";
  assert.deepEqual(decodeText(text), [{ event: "a", data: "1" }]);
});

test("a blank line without data dispatches nothing and resets the event name", () => {
  assert.deepEqual(decodeText("event: a\n\ndata: x\n\n"), [{ event: "message", data: "x" }]);
  assert.deepEqual(decodeText("\n\n\n"), []);
});

test("an event the stream ends in the middle of is dropped", () => {
  assert.deepEqual(decodeText("event: a\ndata: 1\n"), []);
  assert.deepEqual(decodeText("data: 1\n\nevent: b\ndata: 2"), [{ event: "message", data: "1" }]);
  // A character cut off by the end becomes U+FFFD inside the dropped line, without throwing.
  const cut = bytes("data: 1\n\ndata: é").subarray(0, -1);
  assert.deepEqual(decode([cut]), [{ event: "message", data: "1" }]);
  const events = decode([fixtureBytes("no-done")]);
  assert.deepEqual(events.map((e) => e.event), ["status", "text", "tool_call"]);
});

test("parseSSE reads a stream in chunks", async () => {
  const data = fixtureBytes("basic");
  const probe = streamOf(splitAt(data, [5, 100, 101, 2000]));
  const events = await collect(parseSSE(probe.stream));
  assert.deepEqual(events, decode([data]));
  assert.equal(probe.cancelled(), false);
});

test("aborting parseSSE cancels the body and throws the abort reason", async () => {
  const probe = streamOf([bytes("data: 1\n\n")], { hold: true });
  const controller = new AbortController();
  const seen: string[] = [];
  await assert.rejects(
    (async () => {
      for await (const message of parseSSE(probe.stream, controller.signal)) {
        seen.push(message.data);
        setTimeout(() => controller.abort(), 5); // while the next read waits
      }
    })(),
    { name: "AbortError" },
  );
  assert.deepEqual(seen, ["1"]);
  assert.equal(probe.cancelled(), true);
});

test("an already aborted signal throws before reading", async () => {
  const probe = streamOf([bytes("data: 1\n\n")]);
  const controller = new AbortController();
  controller.abort();
  await assert.rejects(collect(parseSSE(probe.stream, controller.signal)), { name: "AbortError" });
  assert.equal(probe.cancelled(), true);
});

test("breaking out of the loop early cancels the body", async () => {
  const probe = streamOf([bytes("data: 1\n\ndata: 2\n\n")], { hold: true });
  for await (const message of parseSSE(probe.stream)) {
    assert.equal(message.data, "1");
    break;
  }
  assert.equal(probe.cancelled(), true);
});

// ---- parseChatEvent ----

test("every event in the fixtures parses as its own type", () => {
  for (const name of FIXTURES) {
    for (const { event, data } of decode([fixtureBytes(name)])) {
      const parsed = parseChatEvent(event, data);
      assert.ok(parsed, `${name}: ${event}`);
      assert.equal(parsed.type, event);
    }
  }
});

test("the fixtures' payloads come through intact", () => {
  const events = decode([fixtureBytes("basic")]).map((e) => parseChatEvent(e.event, e.data));
  const call = events[2];
  assert.deepEqual(call, {
    type: "tool_call",
    id: "toolu_basic_0",
    name: "find_mistakes",
    input: { event: "Sample Grand Prix", year: 2026, session: "Q" },
  });
  const result = events[3];
  assert.ok(result?.type === "tool_result");
  assert.equal(result.chart?.bundle, "find-mistakes");
  assert.equal(result.chart?.resource_uri, "ui://race-engineer/find-mistakes.html");
  assert.equal((result.chart?.data as { event: string }).event, "Sample Grand Prix");
  assert.ok(events[4]?.type === "text" && events[4].delta.includes("🏁"));
  assert.ok(events[6]?.type === "text" && events[6].delta.includes("é escaped"));
  const done = events[7];
  assert.ok(done?.type === "done");
  assert.equal(done.history.length, 4);
  assert.equal(done.questions_left, 7);
  assert.deepEqual(done.usage, { input: 2, output: 125, cache_read: 5499, cache_write: 371 });
  assert.equal(done.model, "scripted");
});

test("unknown events, bad JSON and non-objects are skipped", () => {
  assert.equal(parseChatEvent("message", '{"text": "x"}'), null);
  assert.equal(parseChatEvent("thinking", '{"text": "x"}'), null);
  assert.equal(parseChatEvent("status", "{not json"), null);
  assert.equal(parseChatEvent("status", "NaN"), null);
  assert.equal(parseChatEvent("status", '"text"'), null);
  assert.equal(parseChatEvent("status", '["text"]'), null);
  assert.equal(parseChatEvent("status", '{"text": 3}'), null);
  assert.equal(parseChatEvent("tool_call", '{"name": "x", "input": {}}'), null);
  assert.equal(isChatEventType("done"), true);
  assert.equal(isChatEventType("message"), false);
});

test("fields that are only shown get defaults; the ones the reducer needs are required", () => {
  assert.deepEqual(parseChatEvent("tool_call", '{"id": "t", "name": "x", "input": [1]}'), {
    type: "tool_call",
    id: "t",
    name: "x",
    input: {},
  });
  assert.deepEqual(
    parseChatEvent("tool_result", '{"id": "t", "name": "x", "chart": {"bundle": "find-mistakes"}}'),
    { type: "tool_result", id: "t", name: "x", is_error: false, summary: "", chart: null },
  );
  const failed = parseChatEvent(
    "tool_result",
    '{"id": "t", "name": "x", "is_error": true, "summary": "no", "chart": {"bundle": "b", "data": 1}}',
  );
  assert.ok(failed?.type === "tool_result" && failed.chart === null);
  assert.deepEqual(parseChatEvent("retry", "{}"), { type: "retry", message: "" });
  assert.deepEqual(parseChatEvent("refusal", '{"message": "m"}'), {
    type: "refusal",
    message: "m",
    category: null,
  });
  assert.deepEqual(parseChatEvent("error", '{"message": "m"}'), {
    type: "error",
    code: "unknown",
    message: "m",
  });
  assert.equal(parseChatEvent("done", '{"signature": "s", "questions_left": 1}'), null);
  assert.equal(parseChatEvent("done", '{"history": [], "questions_left": 1}'), null);
  assert.equal(parseChatEvent("done", '{"history": [], "signature": "s"}'), null);
  assert.deepEqual(parseChatEvent("done", '{"history": [], "signature": "s", "questions_left": 8}'), {
    type: "done",
    history: [],
    signature: "s",
    questions_used: 0,
    questions_left: 8,
    stop_reason: null,
    model: "",
    usage: { input: 0, output: 0, cache_read: 0, cache_write: 0 },
    cost_usd: null,
    request_id: "",
    quota: null,
  });
});

test("done's quota (M7): both counts, or null when the limits are off or it doesn't fit", () => {
  const base = '"history": [], "signature": "s", "questions_left": 7';
  const quota = (json: string) => {
    const event = parseChatEvent("done", `{${base}${json}}`);
    assert.ok(event?.type === "done");
    return event.quota;
  };
  assert.deepEqual(quota(', "quota": {"hour_left": 9, "day_left": 24}'), { hour_left: 9, day_left: 24 });
  assert.equal(quota(', "quota": null'), null);
  assert.equal(quota(""), null);
  assert.equal(quota(', "quota": {"hour_left": 9}'), null);
  assert.equal(quota(', "quota": {"hour_left": "9", "day_left": 24}'), null);
});

test("parseChatStream yields typed events and skips unknown ones", async () => {
  const text = "event: hello\ndata: {}\n\n" + new TextDecoder().decode(fixtureBytes("refusal"));
  const events = await collect(parseChatStream(streamOf([bytes(text)]).stream));
  assert.deepEqual(
    events.map((e) => e.type),
    ["status", "text", "refusal", "done"],
  );
});
