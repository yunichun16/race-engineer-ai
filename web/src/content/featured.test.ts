import { test } from "node:test";
import assert from "node:assert/strict";
import { existsSync, readFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { sessionName } from "../lib/format.ts";
import {
  cornerParams,
  explainParam,
  FEATURED,
  featuredHref,
  featuredTags,
  featuredTitle,
  LANDING_CORNER,
  typeTag,
  type FeaturedCorner,
} from "./featured.ts";

const HERE = path.dirname(fileURLToPath(import.meta.url));
// The repository's report/ folder (web/src/content → ../../../report). RE_REPORT_DIR points
// elsewhere, for a copy of web/ that lives outside the repository.
const REPORT_DIR = process.env.RE_REPORT_DIR ?? path.resolve(HERE, "../../../report");
const read = (file: string) => readFileSync(path.join(REPORT_DIR, file), "utf8");

/** A small CSV reader: a header row, commas, and double-quoted fields with "" for a quote. */
function parseCsv(text: string): Record<string, string>[] {
  const rows: string[][] = [];
  let row: string[] = [];
  let field = "";
  let quoted = false;
  for (let i = 0; i < text.length; i++) {
    const c = text[i];
    if (quoted) {
      if (c === '"' && text[i + 1] === '"') {
        field += '"';
        i++;
      } else if (c === '"') quoted = false;
      else field += c;
    } else if (c === '"') quoted = true;
    else if (c === ",") {
      row.push(field);
      field = "";
    } else if (c === "\n" || c === "\r") {
      if (c === "\r" && text[i + 1] === "\n") i++;
      row.push(field);
      rows.push(row);
      row = [];
      field = "";
    } else field += c;
  }
  if (field || row.length) {
    row.push(field);
    rows.push(row);
  }
  const [head, ...body] = rows.filter((r) => r.some((cell) => cell !== ""));
  return body.map((r) => Object.fromEntries(head.map((name, k) => [name, r[k] ?? ""])));
}

// The review queue only: the tool's ranking of 2026's top findings. The reviewer's labels
// (m4_review_labels.json) are never read here or anywhere on the site (plan 13).
const queue = new Map(parseCsv(read("m4_review_queue.csv")).map((r) => [r.id, r]));
const collapse = (s: string) => s.replace(/\s+/g, " ").trim();
const mistakesReport = read("m4_mistakes.md");

const flagged = FEATURED.filter((f) => f.kind === "flagged");

test("the review queue loads", () => {
  assert.equal(queue.size, 50);
  assert.ok(queue.has("2026_15_R_1_16_T5"));
});

test("ids are unique and safe as snapshot file names", () => {
  const ids = FEATURED.map((f) => f.id);
  assert.equal(new Set(ids).size, ids.length);
  for (const id of ids) assert.match(id, /^[A-Za-z0-9_-]+$/);
});

test("each flagged entry is a row of the review queue (event, session, driver, lap, corner, type, inspected)", () => {
  assert.equal(flagged.length, 5);
  for (const f of flagged) {
    const row = queue.get(f.id);
    assert.ok(row, `${f.id} isn't in m4_review_queue.csv`);
    const [year, , session, , lap, turn] = f.id.split("_");
    assert.equal(f.args.year, Number(year), `${f.id} year`);
    assert.equal(f.args.session, session, `${f.id} session in the id`);
    assert.equal(f.args.lap, Number(lap), `${f.id} lap in the id`);
    assert.equal(`T${f.args.turn}`, turn, `${f.id} turn in the id`);
    assert.deepEqual(
      {
        event: f.args.event,
        session: f.args.session,
        driver: f.args.driver,
        lap: String(f.args.lap),
        corner: f.args.turn,
        type: f.typeLabel,
        inspected: f.inspected ? "True" : "False",
      },
      {
        event: row.event,
        session: row.session,
        driver: row.driver,
        lap: row.lap,
        corner: row.corner_label,
        type: row.type_label,
        inspected: row.inspected,
      },
      f.id,
    );
  }
});

test("nothing records or shows a verdict (plan 13)", () => {
  for (const f of FEATURED) {
    for (const key of ["verdict", "video", "reviewRank", "rank"]) {
      assert.ok(!(key in f), `${f.id} has a "${key}" field`);
    }
    const words = [f.blurb, ...featuredTags(f), featuredTitle(f)].join(" ");
    assert.doesNotMatch(words, /review|replay|video|verdict|checked|confirmed|verified|real mistake|not a mistake/i, f.id);
  }
  // The pit-lane example (LAW, British GP Q) is dropped: picking it reveals a verdict.
  assert.ok(!FEATURED.some((f) => f.args.driver === "LAW"));
});

test("entry 0, the landing example, is a flagged corner the tool can draw, with a name for prose", () => {
  assert.equal(LANDING_CORNER, FEATURED[0]);
  assert.equal(LANDING_CORNER.kind, "flagged");
  assert.equal(LANDING_CORNER.name, "Norris");
  assert.equal(LANDING_CORNER.inspected, false);
  assert.deepEqual(cornerParams(LANDING_CORNER), {
    event: "Azerbaijan Grand Prix",
    driver: "NOR",
    lap: 16,
    corner: "5",
    year: 2026,
    session: "R",
  });
});

test("the energy-management example matches its m4_mistakes.md example", () => {
  const f = FEATURED.find((e) => e.kind === "energy") as FeaturedCorner;
  const { year, event, session, driver, lap, turn } = f.args;
  // The example's own list item (one line in the file), so the words can't come from a later one.
  const start = `- \`lift_and_coast\`: ${year} ${event} ${session}, ${driver}: Lap ${lap}, turn ${turn}:`;
  const line = mistakesReport.split("\n").find((l) => l.startsWith(start));
  assert.ok(line, `no line starting ${start} in m4_mistakes.md`);
  assert.ok(collapse(line).includes("likely energy management (the 2026 cars harvest by lifting), not a mistake"), line);
});

test("the saved-time example matches its m4_mistakes.md known incident", () => {
  const f = FEATURED.find((e) => e.kind === "saved-time") as FeaturedCorner;
  const { year, event, session, driver, lap, turn } = f.args;
  const start = `| ${year} ${event} ${sessionName(session)}, ${driver} lap ${lap} T${turn} |`;
  const row = mistakesReport.split("\n").find((l) => l.startsWith(start));
  assert.ok(row, `no row starting ${start} in m4_mistakes.md`);
  for (const words of [`listed #1 of 1 for ${driver}`, "not slower than usual (0.73 s quicker)", `lap time deleted for track limits at turn ${turn}`]) {
    assert.ok(row.includes(words), `the row doesn't say "${words}"`);
  }
});

test("the nothing-flagged example is a whole session, with no corner", () => {
  const f = FEATURED.find((e) => e.kind === "nothing-flagged") as FeaturedCorner;
  assert.equal(f.args.lap, undefined);
  assert.equal(f.args.turn, undefined);
  assert.equal(f.typeLabel, undefined);
  assert.equal(cornerParams(f), null);
  // Its evidence is a live check (site-smoke), noted in a comment in featured.ts.
});

test("every entry with a corner has a lap, a turn and a type; blurbs carry no numbers", () => {
  for (const f of FEATURED) {
    assert.doesNotMatch(f.blurb, /\d/, `${f.id}: a number in the blurb`);
    assert.ok(f.blurb.length > 0);
    if (f.kind === "nothing-flagged") continue;
    assert.ok(f.args.lap !== undefined && f.args.turn !== undefined && f.typeLabel, `${f.id} needs a lap, a turn and a type`);
  }
});

test("each card links to its corner in the explorer, or to the driver's session", () => {
  assert.equal(
    featuredHref(LANDING_CORNER),
    "/mistakes?event=Azerbaijan+Grand+Prix&explain=NOR:16:5&session=R&year=2026",
  );
  const lec = FEATURED.find((f) => f.kind === "nothing-flagged") as FeaturedCorner;
  assert.equal(featuredHref(lec), "/mistakes?driver=LEC&event=Italian+Grand+Prix&session=Q&year=2025");
  for (const f of FEATURED) {
    const params = new URL(featuredHref(f), "http://localhost:3000").searchParams;
    assert.equal(params.get("year"), String(f.args.year), f.id);
    assert.equal(params.get("event"), f.args.event, f.id);
    assert.equal(params.get("session"), f.args.session, f.id);
    if (f.args.lap !== undefined && f.args.turn !== undefined) {
      // The explorer's format: DRV:LAP:TURN, turn upper-cased with no leading "T" (plan 7.3).
      assert.match(params.get("explain") ?? "", /^[A-Z]{3}:\d+:[0-9]+[A-Z]?$/, f.id);
      assert.equal(params.get("driver"), null, f.id);
    } else {
      assert.equal(params.get("driver"), f.args.driver, f.id);
    }
  }
  assert.equal(explainParam("LEC", 10, "t9a"), "LEC:10:9A");
});

test("card titles and tags", () => {
  assert.equal(featuredTitle(LANDING_CORNER), "NOR · Azerbaijan GP 2026 · Race · lap 16, T5");
  const lec = FEATURED.find((f) => f.id === "lec-2025-italian-q") as FeaturedCorner;
  assert.equal(featuredTitle(lec), "LEC · Italian GP 2025 · Qualifying");
  assert.equal(typeTag("track limits / off track (race control)"), "Track limits");
  assert.equal(typeTag("lift and coast (likely energy or fuel management)"), "Lift and coast");
  assert.deepEqual(
    FEATURED.map(featuredTags),
    [
      ["Over-slowing"],
      ["Over-slowing"],
      ["Track limits"],
      ["Track limits"],
      ["Track limits"],
      ["Lift and coast", "Energy management"],
      ["Track limits", "Saved time"],
      ["Nothing flagged"],
    ],
  );
});

test("the snapshot script writes only to the gitignored snapshot folder", () => {
  const gitignore = readFileSync(path.resolve(HERE, "../../.gitignore"), "utf8");
  assert.ok(gitignore.split("\n").includes("/public/snapshots/"), "web/.gitignore must ignore /public/snapshots/");
  const script = readFileSync(path.resolve(HERE, "../../scripts/snapshots.ts"), "utf8");
  assert.ok(existsSync(path.resolve(HERE, "../../scripts/snapshots.ts")));
  assert.match(script, /public\/snapshots/);
});
