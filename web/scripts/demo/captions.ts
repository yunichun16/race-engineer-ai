/**
 * The files `record.ts` hands to `encode.swift` (plan 12), and the checks on them. Pure logic, so
 * `captions.test.ts` covers it without a browser:
 *
 * - `frames/timing.json`: every captured frame with its time in the video (a frame shows until
 *   the next one starts; identical frames are stored once), the scene boundaries the encoder
 *   cross-fades over, and the video's length;
 * - `captions.json`: each scene's caption over the time the scene is on screen.
 *
 * Times are seconds from the start of the video, rounded to the millisecond.
 */

import type { Scene } from "./storyboard.ts";

export interface Frame {
  t: number;
  file: string;
}

export interface SceneTiming {
  id: string;
  start: number;
  end: number;
}

export interface Timeline {
  v: 1;
  width: number;
  height: number;
  /** The output frame rate (the capture rate is whatever the browser kept up with). */
  fps: number;
  duration: number;
  frames: Frame[];
  scenes: SceneTiming[];
  /** Where one scene gives way to the next: the encoder cross-fades there. */
  cuts: number[];
}

export interface Caption {
  start: number;
  end: number;
  text: string;
}

export const ms = (seconds: number) => Math.round(seconds * 1000) / 1000;

/** The timing file: frames in time order, the scenes, and a cut at every boundary between them. */
export function timeline(options: {
  width: number;
  height: number;
  fps: number;
  frames: readonly Frame[];
  scenes: readonly SceneTiming[];
}): Timeline {
  const scenes = options.scenes.map((s) => ({ id: s.id, start: ms(s.start), end: ms(s.end) }));
  const duration = scenes.length ? scenes[scenes.length - 1].end : 0;
  // In time order (screencast frames can arrive out of order), and the first one is the opening
  // frame, whatever millisecond it was stamped.
  const frames = options.frames
    .map((f) => ({ t: ms(f.t), file: f.file }))
    .sort((a, b) => a.t - b.t)
    .map((f, i) => (i === 0 ? { ...f, t: 0 } : f));
  return {
    v: 1,
    width: options.width,
    height: options.height,
    fps: options.fps,
    duration,
    frames,
    scenes,
    cuts: scenes.slice(1).map((s) => s.start),
  };
}

/**
 * Each scene's caption over its time on screen. With the scripted chat (a local draft) a scene
 * with a `scriptedCaption` uses it, so the draft never claims Claude answered.
 */
export function captionTrack(timings: readonly SceneTiming[], scenes: readonly Scene[], scripted: boolean): Caption[] {
  const byId = new Map(scenes.map((scene) => [scene.id, scene]));
  const captions: Caption[] = [];
  for (const timing of timings) {
    const scene = byId.get(timing.id);
    const text = scripted ? (scene?.scriptedCaption ?? scene?.caption) : scene?.caption;
    if (text) captions.push({ start: ms(timing.start), end: ms(timing.end), text });
  }
  return captions;
}

/** What's wrong with a timeline and its captions, as sentences; empty when the encoder can take them. */
export function problems(line: Timeline, captions: readonly Caption[]): string[] {
  const found: string[] = [];
  if (line.frames.length === 0) found.push("no frames were captured");
  else if (line.frames[0].t !== 0) found.push(`the first frame starts at ${line.frames[0].t} s, not 0`);
  for (let i = 1; i < line.frames.length; i++) {
    if (line.frames[i].t < line.frames[i - 1].t) {
      found.push(`frame ${i} (${line.frames[i].file}) goes back in time`);
      break;
    }
  }
  const last = line.frames.at(-1);
  if (last && last.t > line.duration) found.push(`the last frame starts after the end (${last.t} s > ${line.duration} s)`);
  for (let i = 0; i < line.scenes.length; i++) {
    const scene = line.scenes[i];
    if (scene.end <= scene.start) found.push(`scene ${scene.id} has no length`);
    if (i > 0 && scene.start !== line.scenes[i - 1].end) found.push(`scene ${scene.id} doesn't start where ${line.scenes[i - 1].id} ends`);
  }
  for (const caption of captions) {
    if (caption.start < 0 || caption.end > line.duration || caption.end <= caption.start) {
      found.push(`the caption "${caption.text}" runs ${caption.start}-${caption.end} s, outside the video`);
    }
  }
  return found;
}
