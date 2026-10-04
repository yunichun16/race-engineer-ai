// The example questions (content/questions.ts), which the landing page and the chat both offer.

import assert from "node:assert/strict";
import { test } from "node:test";

import { LANDING_QUESTIONS, QUESTION_GROUPS, QUESTIONS, chipLabel } from "../../content/questions.ts";
import { MAX_MESSAGE_CHARS } from "./types.ts";

test("the ids are the stable ones M7 maps saved answers to, in order", () => {
  assert.deepEqual(
    QUESTIONS.map((q) => q.id),
    ["latest", "monaco-2023", "last-race-mistakes", "abu-dhabi-gain", "ham-lec-style", "baku-sc", "out-of-scope"],
  );
});

test("every question's saved answer is its own id, a name the API accepts (plan 4.4)", () => {
  for (const q of QUESTIONS) {
    assert.equal(q.saved, q.id);
    assert.match(q.id, /^[a-z0-9-]{1,40}$/);
  }
});

test("three landing chips with short labels, four for a phone, every group listed and used", () => {
  assert.deepEqual(
    LANDING_QUESTIONS.map((q) => q.id),
    ["monaco-2023", "abu-dhabi-gain", "ham-lec-style"],
  );
  for (const q of LANDING_QUESTIONS) assert.ok(q.chip && q.chip.length < q.text.length, q.id);
  assert.deepEqual(
    QUESTIONS.filter((q) => q.phone).map((q) => q.id),
    ["monaco-2023", "last-race-mistakes", "abu-dhabi-gain", "ham-lec-style"],
  );
  const groups = QUESTION_GROUPS.map((g) => g.id);
  assert.deepEqual(groups, ["races", "mistakes", "head-to-head", "style", "out-of-scope"]);
  for (const group of groups) assert.ok(QUESTIONS.some((q) => q.group === group), group);
  assert.equal(QUESTIONS.find((q) => q.id === "out-of-scope")?.hint, "to see it decline");
});

test("every question can be sent as it is, and a chip falls back to the question", () => {
  for (const q of QUESTIONS) {
    assert.ok(q.text.trim() === q.text && q.text.length > 0 && q.text.length <= MAX_MESSAGE_CHARS, q.id);
    assert.equal(chipLabel(q), q.chip ?? q.text);
  }
});
