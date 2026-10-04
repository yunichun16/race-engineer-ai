// The demo recorder's pure parts: the timing file, the caption track, their checks, and the
// storyboard's lengths and end card. No browser: `node --test scripts/demo/captions.test.ts`.

import assert from "node:assert/strict";
import { describe, test } from "node:test";
import { captionTrack, problems, timeline, type SceneTiming } from "./captions.ts";
import { bareLink, endCard, onCameraLength, onCameraStep, plannedLength, STORYBOARD, type Scene } from "./storyboard.ts";

const SCENES: SceneTiming[] = [
  { id: "a", start: 0, end: 4.0004 },
  { id: "b", start: 4.0004, end: 9 },
];

const STORY: Scene[] = [
  { id: "a", caption: "First.", s: 4, steps: [] },
  { id: "b", caption: "Claude answers.", scriptedCaption: "The chat answers.", s: 5, steps: [] },
  { id: "c", caption: null, s: 2, steps: [] },
];

describe("timeline", () => {
  test("puts frames in time order, opens at 0 and cuts at each scene boundary", () => {
    const line = timeline({
      width: 1280,
      height: 720,
      fps: 30,
      frames: [
        { t: 2.5, file: "000002.jpg" },
        { t: 0.012, file: "000000.jpg" },
        { t: 1.25, file: "000001.jpg" },
      ],
      scenes: SCENES,
    });
    assert.deepEqual(
      line.frames.map((f) => [f.t, f.file]),
      [
        [0, "000000.jpg"],
        [1.25, "000001.jpg"],
        [2.5, "000002.jpg"],
      ],
    );
    assert.equal(line.duration, 9);
    assert.deepEqual(line.cuts, [4]);
    assert.deepEqual(line.scenes[0], { id: "a", start: 0, end: 4 });
    assert.deepEqual(problems(line, captionTrack(line.scenes, STORY, false)), []);
  });

  test("an empty recording has no length", () => {
    const line = timeline({ width: 1, height: 1, fps: 30, frames: [], scenes: [] });
    assert.equal(line.duration, 0);
    assert.deepEqual(line.cuts, []);
  });
});

describe("captionTrack", () => {
  test("gives each captioned scene its caption over its time on screen", () => {
    assert.deepEqual(captionTrack([...SCENES, { id: "c", start: 9, end: 11 }], STORY, false), [
      { start: 0, end: 4, text: "First." },
      { start: 4, end: 9, text: "Claude answers." },
    ]);
  });

  test("the scripted chat's draft uses the scene's scripted caption, so it never claims Claude answered", () => {
    const texts = captionTrack(SCENES, STORY, true).map((c) => c.text);
    assert.deepEqual(texts, ["First.", "The chat answers."]);
  });

  test("the real storyboard's scripted chat caption doesn't name Claude", () => {
    const chat = STORYBOARD.find((scene) => scene.id === "chat");
    assert.ok(chat?.caption?.includes("Claude"));
    assert.ok(chat?.scriptedCaption && !chat.scriptedCaption.includes("Claude"));
  });
});

describe("problems", () => {
  const good = () =>
    timeline({
      width: 1280,
      height: 720,
      fps: 30,
      frames: [
        { t: 0, file: "0.jpg" },
        { t: 3, file: "1.jpg" },
      ],
      scenes: SCENES,
    });

  test("names a recording with no frames", () => {
    const line = { ...good(), frames: [] };
    assert.deepEqual(problems(line, []), ["no frames were captured"]);
  });

  test("names frames that go back in time or start after the end", () => {
    const line = good();
    line.frames = [
      { t: 0, file: "0.jpg" },
      { t: 5, file: "1.jpg" },
      { t: 4, file: "2.jpg" },
      { t: 12, file: "3.jpg" },
    ];
    const found = problems(line, []);
    assert.ok(found.some((p) => p.includes("2.jpg") && p.includes("back in time")));
    assert.ok(found.some((p) => p.includes("after the end")));
  });

  test("names scenes that don't follow on and captions outside the video", () => {
    const line = good();
    line.scenes = [
      { id: "a", start: 0, end: 4 },
      { id: "b", start: 4.5, end: 9 },
    ];
    const found = problems(line, [{ start: 8, end: 9.5, text: "Too long." }]);
    assert.ok(found.some((p) => p.includes("scene b doesn't start where a ends")));
    assert.ok(found.some((p) => p.includes('"Too long."')));
  });
});

describe("the storyboard", () => {
  test("runs about 75 seconds, ends on the end card and names each scene once", () => {
    assert.equal(plannedLength(), 75);
    assert.equal(plannedLength(STORYBOARD, new Set(["chat"])), 55);
    const ids = STORYBOARD.map((scene) => scene.id);
    assert.equal(new Set(ids).size, ids.length);
    const last = STORYBOARD.at(-1);
    assert.equal(last?.id, "end");
    assert.equal(last?.caption, null);
    assert.ok(last?.steps.some((step) => step.kind === "endcard"));
  });

  test("every scene's steps fit its length even when each live wait runs to the end", () => {
    for (const scene of STORYBOARD) {
      assert.ok(onCameraLength(scene) <= scene.s, `${scene.id}: ${onCameraLength(scene)} s of steps in ${scene.s} s`);
    }
  });

  test("off-camera steps take no time on screen; a live wait counts in full", () => {
    assert.equal(onCameraStep({ kind: "goto", path: "/", ready: "true", what: "x" }), 0);
    assert.equal(onCameraStep({ kind: "wait", until: "true", what: "x" }), 0);
    assert.equal(onCameraStep({ kind: "wait", until: "true", what: "x", live: 4 }), 4.1);
    assert.equal(onCameraStep({ kind: "hold", s: 2 }), 2);
    assert.equal(Math.round(onCameraStep({ kind: "type", target: "null", text: "hi", s: 2 }) * 1000) / 1000, 2.95);
  });

  test("captions are one sentence each and short enough for two lines at 1280 px", () => {
    for (const scene of STORYBOARD) {
      for (const text of [scene.caption, scene.scriptedCaption]) {
        if (!text) continue;
        assert.ok(text.length <= 110, `${scene.id}: ${text.length} characters`);
        assert.match(text, /^[A-Z].*\.$/);
      }
    }
  });
});

describe("the end card", () => {
  test("carries the site's name, its address, the fan-project line and the credit (no email)", () => {
    const card = endCard("https://race-engineer.vercel.app/");
    assert.equal(card.name, "Race Engineer AI");
    assert.equal(card.address, "race-engineer.vercel.app");
    assert.match(card.fanProject, /fan project/i);
    assert.ok(card.credit?.startsWith("Built by "));
    assert.ok(card.credit?.includes("github.com/"));
    assert.ok(!card.credit?.includes("@"));
  });

  test("a label replaces the address", () => {
    assert.equal(endCard("http://localhost:3000", "example.org").address, "example.org");
  });

  test("bareLink drops the scheme, www and trailing slashes", () => {
    assert.equal(bareLink("https://www.linkedin.com/in/someone/"), "linkedin.com/in/someone");
    assert.equal(bareLink("http://localhost:3000"), "localhost:3000");
  });
});
