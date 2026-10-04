/**
 * Saved example answers (plan 4.1-4.2): records the chat answering the example questions, checks
 * the recordings, and copies the API's into the site at build time.
 *
 *   node scripts/saved.ts record --api <url> [--out ../data/saved] [--ids a,b] [--allow-fake]
 *   node scripts/saved.ts check [--dir ../data/saved]
 *   node scripts/saved.ts pull --api <url> [--out public/saved] [--tolerant] [--allow-fake]
 *
 * record asks each question of content/questions.ts in a new conversation, through the site's
 * own chat client and parser (lib/chat/client.ts, sse.ts), and keeps a recording only when `done`
 * came last, no error, retry or refusal came, the question's tool answered (none for the
 * out-of-scope one) and every chart passes its chart's guard. A failed question is asked once
 * more, then reported. Each recording is written atomically as <out>/<id>.json, with done's
 * history and signature emptied (large, and of no use outside the conversation). It prints a
 * table (id, tools, KB, cost, seconds) and exits 1 when any question has no recording. It refuses
 * the scripted chat (chat.mode "fake") unless --allow-fake: those recordings are for building
 * and testing the page, and the API serves them only with RACE_ENGINEER_SAVED_ALLOW_FAKE=1.
 * A real model costs money (about $0.05-0.15 for the seven), so record runs only against the
 * API named by --api, never a default.
 *
 * check validates every recording in a folder with the site's own guard, the charts' guards and
 * the tool rule above, checks an index.json against the files when there is one, and warns when a
 * recording's question no longer matches content/questions.ts. Exit 1 on an error.
 *
 * pull copies the API's index and recordings (GET /api/saved, /api/saved/{id}) into the site's
 * public/saved, checked, for the static build to serve as /saved/index.json and /saved/{id}.json;
 * scripted recordings only with --allow-fake. With --tolerant a failure is a warning and the exit
 * is 0, so the deploy build never fails on it (the chat then reads the API, or offers none);
 * without it, nothing is written unless every recording came. public/saved and data/saved are
 * gitignored: the charts carry data drawn from car positions.
 */

import { mkdir, readdir, readFile, rename, rm, writeFile } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { isChartName } from "../src/components/charts/chart-names.ts";
import { QUESTIONS } from "../src/content/questions.ts";
import { ApiError, TOOL_GUARDS } from "../src/lib/api/client.ts";
import { loadHealth } from "../src/lib/api/health.ts";
import type { ToolName } from "../src/lib/api/types.ts";
import { streamChat } from "../src/lib/chat/client.ts";
import { parseSavedAnswer, parseSavedIndex, SAVED_ID, type SavedAnswer, type SavedIndexEntry } from "../src/lib/chat/saved.ts";
import type { ChatEvent, DoneEvent } from "../src/lib/chat/types.ts";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const DATA_SAVED = path.resolve(HERE, "../../data/saved");
const PUBLIC_SAVED = path.resolve(HERE, "../public/saved");
const ANSWER_TIMEOUT_MS = 180_000; // a real answer with a cold heavy tool
const GET_TIMEOUT_MS = 30_000;

/** The tools each example question is there for (any one of them); [] means no tool at all. */
const EXPECTED_TOOLS: Readonly<Record<string, readonly string[]>> = {
  latest: ["find_session", "list_sessions"],
  "monaco-2023": ["get_race_summary"],
  "last-race-mistakes": ["find_mistakes"],
  "abu-dhabi-gain": ["compare_laps"],
  "ham-lec-style": ["compare_driving_styles"],
  "baku-sc": ["get_race_summary"],
  "out-of-scope": [],
};

// ---- Arguments ----

interface Args {
  command: string;
  api: string | null;
  out: string | null;
  dir: string | null;
  ids: string[] | null;
  allowFake: boolean;
  tolerant: boolean;
}

function parseArgs(argv: string[]): Args {
  const value = (flag: string): string | null => {
    const i = argv.indexOf(flag);
    return i >= 0 && i + 1 < argv.length ? argv[i + 1] : null;
  };
  const ids = value("--ids");
  return {
    command: argv[0] ?? "",
    api: value("--api")?.replace(/\/+$/, "") ?? null,
    out: value("--out"),
    dir: value("--dir"),
    ids: ids === null ? null : ids.split(",").map((id) => id.trim()).filter(Boolean),
    allowFake: argv.includes("--allow-fake"),
    tolerant: argv.includes("--tolerant"),
  };
}

const USAGE = `usage:
  node scripts/saved.ts record --api <url> [--out ../data/saved] [--ids a,b] [--allow-fake]
  node scripts/saved.ts check [--dir ../data/saved]
  node scripts/saved.ts pull --api <url> [--out public/saved] [--tolerant] [--allow-fake]`;

// ---- Shared ----

function table(head: string[], rows: string[][]): string {
  const widths = head.map((h, k) => Math.max(h.length, ...rows.map((r) => r[k].length)));
  const line = (cells: string[]) => cells.map((c, k) => c.padEnd(widths[k])).join("  ").trimEnd();
  return [line(head), ...rows.map(line)].join("\n");
}

function messageOf(error: unknown): string {
  if (error instanceof ApiError) return `${error.code}: ${error.message}`;
  return error instanceof Error ? error.message : String(error);
}

/** Writes `text` to `file` through a temporary file in the same folder, so a reader never sees
 *  half a file. */
async function writeAtomic(file: string, text: string): Promise<void> {
  const tmp = `${file}.tmp-${process.pid}`;
  await writeFile(tmp, text);
  await rename(tmp, file);
}

/** The problems with a recording's events against the rules above: the question's tool must
 *  have answered (none for out-of-scope), and every chart must pass its guard. */
function problems(id: string, events: readonly ChatEvent[]): string[] {
  const out: string[] = [];
  const last = events[events.length - 1];
  if (last?.type !== "done") out.push(`ended with ${last ? `"${last.type}"` : "nothing"}, not done`);
  if (events.filter((e) => e.type === "done").length > 1) out.push("more than one done");
  for (const event of events) {
    if (event.type === "error") out.push(`error ${event.code}: ${event.message}`);
    if (event.type === "retry") out.push("a garbled tool call (retry)");
    if (event.type === "refusal") out.push("a refusal");
    if (event.type === "tool_result" && event.chart !== null) {
      const guard = TOOL_GUARDS[event.name as ToolName] as ((data: unknown) => boolean) | undefined;
      if (!isChartName(event.chart.bundle)) out.push(`${event.name}'s chart "${event.chart.bundle}" isn't one the site draws`);
      else if (guard === undefined || !guard(event.chart.data)) out.push(`${event.name}'s chart fails the site's guard`);
    }
  }
  const expected = EXPECTED_TOOLS[id];
  const called = toolsCalled(events);
  if (expected !== undefined) {
    if (expected.length === 0 && called.length > 0) out.push(`expected no tool, called ${called.join(", ")}`);
    const answered = events.some((e) => e.type === "tool_result" && !e.is_error && expected.includes(e.name));
    if (expected.length > 0 && !answered) out.push(`expected ${expected.join(" or ")} to answer; called ${called.join(", ") || "nothing"}`);
  }
  return out;
}

function toolsCalled(events: readonly ChatEvent[]): string[] {
  return events.flatMap((e) => (e.type === "tool_call" ? [e.name] : []));
}

// ---- record ----

interface Asked {
  events: ChatEvent[];
  seconds: number;
  error: string | null;
}

async function ask(api: string, question: string): Promise<Asked> {
  const started = performance.now();
  const events: ChatEvent[] = [];
  try {
    const stream = streamChat({ message: question, history: [], signature: null }, { apiUrl: api, signal: AbortSignal.timeout(ANSWER_TIMEOUT_MS) });
    for await (const event of stream) events.push(event);
    return { events, seconds: (performance.now() - started) / 1000, error: null };
  } catch (error) {
    return { events, seconds: (performance.now() - started) / 1000, error: messageOf(error) };
  }
}

function recording(
  id: string,
  question: string,
  events: ChatEvent[],
  mode: "anthropic" | "fake",
  data: SavedAnswer["data"],
): SavedAnswer {
  const done = events[events.length - 1] as DoneEvent;
  return {
    v: 1,
    id,
    question,
    recorded_at: new Date().toISOString().replace(/\.\d{3}Z$/, "Z"),
    mode,
    model: done.model,
    prompt_version: done.signature.split(".")[0] ?? "",
    data,
    // The conversation's own history stays out, and the recorder's quota means nothing to a visitor.
    events: events.map((event) => (event.type === "done" ? { ...event, history: [], signature: "", quota: null } : event)),
  };
}

async function record(args: Args): Promise<number> {
  if (args.api === null) {
    console.error(`record needs --api (it asks real questions there).\n${USAGE}`);
    return 2;
  }
  const out = args.out === null ? DATA_SAVED : path.resolve(args.out);
  let health;
  try {
    health = await loadHealth(undefined, { baseUrl: args.api });
  } catch (error) {
    console.error(`Can't read ${args.api}/api/health: ${messageOf(error)}`);
    return 1;
  }
  const mode = health.chat.mode;
  if (mode === "off") {
    console.error(`The chat is off on ${args.api} (chat.mode "off"): nothing to record.`);
    return 1;
  }
  if (mode === "fake" && !args.allowFake) {
    console.error(
      `${args.api} runs the scripted chat (chat.mode "fake"). Its recordings are only for building and testing ` +
        "the page: pass --allow-fake to record them anyway.",
    );
    return 2;
  }
  if (mode !== "fake" && mode !== "anthropic") {
    console.error(`Unknown chat.mode "${mode}" on ${args.api}.`);
    return 1;
  }
  const chatMode: SavedAnswer["mode"] = mode === "fake" ? "fake" : "anthropic";
  const questions = QUESTIONS.filter((q) => args.ids === null || args.ids.includes(q.saved ?? q.id));
  const unknown = (args.ids ?? []).filter((id) => !QUESTIONS.some((q) => (q.saved ?? q.id) === id));
  if (unknown.length > 0) {
    console.error(`Not example question ids: ${unknown.join(", ")}`);
    return 2;
  }
  await mkdir(out, { recursive: true });
  const data = { latest: health.data.latest, results_run: health.data.results_run };

  const rows: string[][] = [];
  let missing = 0;
  for (const q of questions) {
    const id = q.saved ?? q.id;
    let attempt = await ask(args.api, q.text);
    let found = attempt.error !== null ? [attempt.error] : problems(id, attempt.events);
    let tries = 1;
    if (found.length > 0) {
      attempt = await ask(args.api, q.text); // once more
      found = attempt.error !== null ? [attempt.error] : problems(id, attempt.events);
      tries = 2;
    }
    const tools = toolsCalled(attempt.events).join(",") || "-";
    if (found.length > 0) {
      missing++;
      rows.push([id, "FAILED", tools, "", "", attempt.seconds.toFixed(2), `${tries} tries: ${found.join("; ")}`]);
      continue;
    }
    const saved = recording(id, q.text, attempt.events, chatMode, data);
    const text = `${JSON.stringify(saved)}\n`;
    if (parseSavedAnswer(JSON.parse(text)) === null) {
      // The site's own guard must read what was written; this would be a bug in this script.
      missing++;
      rows.push([id, "FAILED", tools, "", "", attempt.seconds.toFixed(2), "the site's guard rejects the recording"]);
      continue;
    }
    await writeAtomic(path.join(out, `${id}.json`), text);
    const done = attempt.events[attempt.events.length - 1] as DoneEvent;
    const cost = done.cost_usd === null ? "" : `$${done.cost_usd.toFixed(4)}`;
    rows.push([
      id,
      "ok",
      tools,
      (Buffer.byteLength(text) / 1024).toFixed(1),
      cost,
      attempt.seconds.toFixed(2),
      tries > 1 ? "second try" : "",
    ]);
  }
  console.log(`Saved answers from ${args.api} (chat.mode ${mode}${mode === "fake" ? ", costs simulated" : ""}) into ${out}`);
  console.log(table(["id", "ok", "tools", "KB", "cost", "s", ""], rows));
  if (missing > 0) console.log(`\n${missing} of ${questions.length} questions have no recording.`);
  return missing > 0 ? 1 : 0;
}

// ---- check ----

async function check(args: Args): Promise<number> {
  const dir = args.dir === null ? DATA_SAVED : path.resolve(args.dir);
  let names: string[];
  try {
    names = (await readdir(dir)).filter((name) => name.endsWith(".json")).sort();
  } catch {
    console.error(`No folder ${dir}. Record first: node scripts/saved.ts record --api <url>`);
    return 1;
  }
  const rows: string[][] = [];
  const answers = new Map<string, SavedAnswer>();
  let errors = 0;
  for (const name of names.filter((n) => n !== "index.json")) {
    const stem = name.slice(0, -".json".length);
    const notes: string[] = [];
    const errs: string[] = [];
    let raw: unknown = null;
    try {
      raw = JSON.parse(await readFile(path.join(dir, name), "utf8"));
    } catch (error) {
      errs.push(`not JSON: ${messageOf(error)}`);
    }
    const answer = errs.length === 0 ? parseSavedAnswer(raw) : null;
    if (errs.length === 0 && answer === null) errs.push("not a saved answer the site can show");
    if (answer !== null) {
      if (answer.id !== stem) errs.push(`its id is "${answer.id}"`);
      errs.push(...problems(answer.id, answer.events));
      const example = QUESTIONS.find((q) => (q.saved ?? q.id) === answer.id);
      if (example === undefined) notes.push("not one of the example questions");
      else if (example.text !== answer.question) notes.push(`question changed in questions.ts: now "${example.text}"`);
      if (answer.mode === "fake") notes.push("scripted");
      answers.set(answer.id, answer);
    }
    if (!SAVED_ID.test(stem)) errs.push("a file name the API won't serve");
    if (errs.length > 0) errors++;
    const tools = answer ? toolsCalled(answer.events).join(",") || "-" : "";
    rows.push([stem, errs.length > 0 ? "ERROR" : "ok", answer?.recorded_at ?? "", tools, [...errs, ...notes].join("; ")]);
  }
  for (const q of QUESTIONS) {
    const id = q.saved ?? q.id;
    if (!answers.has(id) && !rows.some((r) => r[0] === id)) rows.push([id, "missing", "", "", "no recording"]);
  }

  if (names.includes("index.json")) {
    let index: SavedIndexEntry[] | null = null;
    try {
      index = parseSavedIndex(JSON.parse(await readFile(path.join(dir, "index.json"), "utf8")));
    } catch {
      index = null;
    }
    const errs: string[] = [];
    if (index === null) errs.push("not an index");
    else {
      for (const row of index) {
        const answer = answers.get(row.id);
        if (answer === undefined) errs.push(`lists ${row.id}, which has no valid file`);
        else if (answer.question !== row.question || answer.recorded_at !== row.recorded_at || answer.mode !== row.mode) {
          errs.push(`${row.id} differs from its file`);
        }
      }
      for (const id of answers.keys()) if (!index.some((row) => row.id === id)) errs.push(`doesn't list ${id}`);
    }
    if (errs.length > 0) errors++;
    rows.push(["index.json", errs.length > 0 ? "ERROR" : "ok", "", "", errs.join("; ") || `${index?.length ?? 0} answers`]);
  }

  console.log(`Saved answers in ${dir}`);
  console.log(table(["id", "ok", "recorded", "tools", ""], rows));
  return errors > 0 ? 1 : 0;
}

// ---- pull ----

async function getText(url: string): Promise<{ text: string } | { error: string }> {
  try {
    const response = await fetch(url, { signal: AbortSignal.timeout(GET_TIMEOUT_MS) });
    const text = await response.text();
    if (!response.ok) return { error: `HTTP ${response.status}: ${text.slice(0, 160)}` };
    return { text };
  } catch (error) {
    return { error: messageOf(error) };
  }
}

async function pull(args: Args): Promise<number> {
  if (args.api === null) {
    console.error(`pull needs --api.\n${USAGE}`);
    return 2;
  }
  const out = args.out === null ? PUBLIC_SAVED : path.resolve(args.out);
  const failed = (message: string): number => {
    console[args.tolerant ? "warn" : "error"](`${args.tolerant ? "Warning: " : ""}${message}`);
    return args.tolerant ? 0 : 1;
  };

  const got = await getText(`${args.api}/api/saved`);
  if ("error" in got) return failed(`no saved answers from ${args.api}/api/saved (${got.error}); nothing written`);
  let index: SavedIndexEntry[] | null;
  try {
    index = parseSavedIndex(JSON.parse(got.text));
  } catch {
    index = null;
  }
  if (index === null) return failed(`${args.api}/api/saved isn't an index; nothing written`);

  const rows: string[][] = [];
  const files: { id: string; text: string; row: SavedIndexEntry }[] = [];
  let problemsSeen = 0;
  for (const row of index) {
    if (row.mode === "fake" && !args.allowFake) {
      rows.push([row.id, "skipped", "", "scripted (pass --allow-fake to copy it)"]);
      continue;
    }
    const file = await getText(`${args.api}/api/saved/${row.id}`);
    let answer: SavedAnswer | null = null;
    if ("text" in file) {
      try {
        answer = parseSavedAnswer(JSON.parse(file.text));
      } catch {
        answer = null;
      }
    }
    if (!("text" in file) || answer === null || answer.id !== row.id) {
      problemsSeen++;
      rows.push([row.id, "FAILED", "", "error" in file ? file.error : "not a saved answer the site can show"]);
      continue;
    }
    files.push({ id: row.id, text: file.text, row: { ...row, question: answer.question, recorded_at: answer.recorded_at, mode: answer.mode } });
    rows.push([row.id, "ok", (Buffer.byteLength(file.text) / 1024).toFixed(1), answer.mode === "fake" ? "scripted" : ""]);
  }

  console.log(`Saved answers from ${args.api} into ${out}`);
  console.log(table(["id", "ok", "KB", ""], rows));
  if (problemsSeen > 0 && !args.tolerant) {
    console.error(`${problemsSeen} recordings didn't come; nothing written.`);
    return 1;
  }
  if (files.length === 0) {
    console.log("No recordings to copy; nothing written.");
    return 0;
  }
  // Written beside the folder, then swapped in whole, so the build never sees a mix.
  const tmp = `${out}.tmp-${process.pid}`;
  await rm(tmp, { recursive: true, force: true });
  await mkdir(tmp, { recursive: true });
  for (const file of files) await writeFile(path.join(tmp, `${file.id}.json`), file.text);
  await writeFile(path.join(tmp, "index.json"), `${JSON.stringify({ answers: files.map((f) => f.row) })}\n`);
  await rm(out, { recursive: true, force: true });
  await rename(tmp, out);
  if (problemsSeen > 0) console.warn(`Warning: ${problemsSeen} recordings didn't come; copied the other ${files.length}.`);
  return 0;
}

// ---- main ----

async function main(args: Args): Promise<number> {
  switch (args.command) {
    case "record":
      return record(args);
    case "check":
      return check(args);
    case "pull":
      return pull(args);
    default:
      console.error(USAGE);
      return 2;
  }
}

const args = parseArgs(process.argv.slice(2));
try {
  process.exitCode = await main(args);
} catch (error) {
  // Only a local problem gets here (the folder can't be written, say): a tolerant pull still
  // doesn't end the build.
  if (!(args.command === "pull" && args.tolerant)) throw error;
  console.warn(`Warning: saved answers skipped: ${messageOf(error)}`);
  process.exitCode = 0;
}
