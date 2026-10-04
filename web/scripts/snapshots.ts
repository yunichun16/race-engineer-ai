/**
 * Saves the featured corners from a running API, so the landing page's example draws without a
 * round trip to it (plan 9.2, spec h).
 *
 *   node scripts/snapshots.ts [--api http://127.0.0.1:8000] [--tolerant]      (or make site-snapshots)
 *
 * For each entry of content/featured.ts with a lap and a turn, it calls explain_corner and writes
 * the API's answer, as is, to public/snapshots/<id>.json; for the landing example (entry 0) it
 * also saves the rest of that session's flagged corners (find_mistakes, the whole field, top 5)
 * as <id>.mistakes.json. From the same answers it writes hero.json, the hero card's rotating
 * real examples (components/landing/hero-examples.ts: only what the card draws, about 11 KB),
 * so the landing's first screen needs no API either. public/snapshots/ is gitignored, because the
 * outlines come from car positions: whether snapshots are committed is an M7 decision. It prints a
 * table (id, ok, flagged, replay, KB) and writes nothing else.
 *
 * It exits with 1 when a call fails, or when the landing example isn't flagged or has no replay
 * (then the landing would show a corner the detectors didn't pick, or a chart without its
 * replay, and entry 1 should move to the front), or when a hero example can't be drawn.
 *
 * `--tolerant` is for the deploy build (`npm run build:vercel`, M7 plan 7.2), where a slow or
 * missing API must not fail the site: every failure is still in the table, an example that can't
 * be drawn is left out of hero.json, the first call that can't reach the API at all skips the
 * rest, and the script exits with 0. The landing then falls back to a live call or its text card.
 * Each file is written whole (a temporary file renamed into place), so a run that stops halfway
 * never leaves half a hero.json, and hero.json isn't written at all without an example to draw.
 */

import { mkdir, rename, writeFile } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { isCornerChart, type CornerChart } from "../src/charts/explain-corner.ts";
import { isMistakeList } from "../src/charts/find-mistakes.ts";
import { heroEntries, heroExample, heroFile, type HeroExample } from "../src/components/landing/hero-examples.ts";
import { cornerParams, FEATURED } from "../src/content/featured.ts";
import { withQuery } from "../src/lib/url.ts";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const OUT = path.resolve(HERE, "../public/snapshots");
const TIMEOUT_MS = 90_000; // a cold explain_corner loads a whole session

function apiBase(argv: string[]): string {
  const i = argv.indexOf("--api");
  const given = i >= 0 ? argv[i + 1] : process.env.NEXT_PUBLIC_API_URL;
  return (given || "http://127.0.0.1:8000").replace(/\/+$/, "");
}

/** Writes the file whole: a reader (or a build) sees the old file or the new one, never part of it. */
async function writeWhole(file: string, text: string): Promise<void> {
  const temp = `${file}.${process.pid}.tmp`;
  await writeFile(temp, text);
  await rename(temp, file);
}

interface Row {
  id: string;
  ok: boolean;
  flagged: string;
  replay: string;
  kb: string;
  note: string;
}

type Got =
  | { text: string; body: { summary?: unknown; data?: unknown } }
  | { error: string; /** The API couldn't be reached at all (refused, no such host, timed out). */ unreachable: boolean };

/** GET a tool's REST route; the parsed body, or the reason it failed. */
async function getTool(base: string, tool: string, params: Record<string, string | number | undefined>): Promise<Got> {
  const url = withQuery(`${base}/api/tools/${tool}`, params);
  let response: Response;
  try {
    response = await fetch(url, { signal: AbortSignal.timeout(TIMEOUT_MS) });
  } catch (error) {
    const cause = error instanceof Error && error.cause instanceof Error ? `: ${error.cause.message}` : "";
    return { error: `${error instanceof Error ? error.message : String(error)}${cause}`, unreachable: true };
  }
  try {
    const text = await response.text();
    if (!response.ok) return { error: `HTTP ${response.status}: ${text.slice(0, 160)}`, unreachable: false };
    return { text, body: JSON.parse(text) as { summary?: unknown; data?: unknown } };
  } catch (error) {
    return { error: error instanceof Error ? error.message : String(error), unreachable: false };
  }
}

async function main(tolerant: boolean): Promise<number> {
  const base = apiBase(process.argv.slice(2));
  await mkdir(OUT, { recursive: true });
  const rows: Row[] = [];
  const corners = new Map<string, CornerChart>();
  let failed = false;
  let unreachable = false; // tolerant: the API couldn't be reached, so the rest are skipped

  for (const [index, entry] of FEATURED.entries()) {
    const params = cornerParams(entry);
    if (!params) continue; // a whole session: nothing to draw
    if (unreachable) {
      rows.push({ id: entry.id, ok: false, flagged: "", replay: "", kb: "", note: "skipped: the API is unreachable" });
      continue;
    }
    const got = await getTool(base, "explain_corner", params);
    if ("error" in got) {
      rows.push({ id: entry.id, ok: false, flagged: "", replay: "", kb: "", note: got.error });
      failed = true;
      unreachable = tolerant && got.unreachable;
      continue;
    }
    const data = got.body.data;
    if (typeof got.body.summary !== "string" || !isCornerChart(data)) {
      rows.push({ id: entry.id, ok: false, flagged: "", replay: "", kb: "", note: "not a corner chart" });
      failed = true;
      continue;
    }
    await writeWhole(path.join(OUT, `${entry.id}.json`), got.text);
    corners.set(entry.id, data);
    const row: Row = {
      id: entry.id,
      ok: true,
      flagged: String(data.flagged),
      replay: data.replay ? "yes" : "no",
      kb: (Buffer.byteLength(got.text) / 1024).toFixed(1),
      note: "",
    };
    if (index === 0 && (data.flagged !== true || !data.replay)) {
      row.note = "the landing example must be flagged and have a replay";
      failed = true;
    }
    rows.push(row);

    if (index === 0) {
      const { event, year, session } = params;
      const list = await getTool(base, "find_mistakes", { event, year, session, limit: 5 });
      const listId = `${entry.id}.mistakes`;
      if ("error" in list || typeof list.body.summary !== "string" || !isMistakeList(list.body.data)) {
        const note = "error" in list ? list.error : "not a mistake list";
        rows.push({ id: listId, ok: false, flagged: "", replay: "", kb: "", note });
        failed = true;
        unreachable = tolerant && "error" in list && list.unreachable;
      } else {
        await writeWhole(path.join(OUT, `${listId}.json`), list.text);
        const kb = (Buffer.byteLength(list.text) / 1024).toFixed(1);
        rows.push({ id: listId, ok: true, flagged: "", replay: "", kb, note: `${list.body.data.mistakes.length} rows` });
      }
    }
  }

  // The hero's examples, in their order: what the card draws of each, in one small file.
  const examples: HeroExample[] = [];
  const skipped: string[] = [];
  for (const entry of heroEntries()) {
    const data = corners.get(entry.id);
    const example = data ? heroExample(data, entry) : null;
    if (example) examples.push(example);
    else skipped.push(entry.id);
  }
  if (examples.length) {
    const text = JSON.stringify(heroFile(examples));
    await writeWhole(path.join(OUT, "hero.json"), text);
    const note = `${examples.length} examples${skipped.length ? `; not drawable: ${skipped.join(", ")}` : ""}`;
    rows.push({ id: "hero", ok: true, flagged: "", replay: "", kb: (Buffer.byteLength(text) / 1024).toFixed(1), note });
  } else rows.push({ id: "hero", ok: false, flagged: "", replay: "", kb: "", note: "no drawable example" });
  if (skipped.length) failed = true;

  const head = ["id", "ok", "flagged", "replay", "KB", ""];
  const table = rows.map((r) => [r.id, r.ok ? "ok" : "FAILED", r.flagged, r.replay, r.kb, r.note]);
  const widths = head.map((h, k) => Math.max(h.length, ...table.map((t) => t[k].length)));
  const line = (cells: string[]) => cells.map((c, k) => c.padEnd(widths[k])).join("  ").trimEnd();
  console.log(`Snapshots from ${base} into ${path.relative(process.cwd(), OUT) || OUT}`);
  console.log(line(head));
  for (const t of table) console.log(line(t));
  if (failed && tolerant) console.log("Tolerant: carrying on without what failed (the landing has its fallbacks).");
  return failed && !tolerant ? 1 : 0;
}

const tolerant = process.argv.includes("--tolerant");
try {
  process.exitCode = await main(tolerant);
} catch (error) {
  // Only a local problem gets here (the folder can't be written, say): still not the build's end.
  if (!tolerant) throw error;
  console.log(`Snapshots skipped: ${error instanceof Error ? error.message : String(error)}`);
  process.exitCode = 0;
}
