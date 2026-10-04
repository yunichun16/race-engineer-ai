# M6: the website and how it was checked

M6 puts the M5 tools in front of anyone with a browser. The Next.js site in `web/` has a landing
page, a chat, two explorers (flagged corners and driving styles) and the findings with every
published report. It is phone first, dark by default with a light theme, and installs as a
home-screen app. Its five charts are the same code that draws them inside Claude: M6 split each
MCP App page into a host-free core that both the MCP bundles and the site import, and the site
wraps each one in a small React component. The engine gained three read-only catalog routes for
the site's pickers; the tool list and the chat's prompt are unchanged. No npm or Python package
was added.

The plan's done-when is "the whole site runs locally in the Browser pane", with FastAPI and
Next.js started from `.claude/launch.json`. That holds. `api-fake` (the API with the scripted
chat), `web` (`next dev`) and `web-prod` (a production build) start from `.claude/launch.json`.
`make site-smoke` passes against both servers, and the click-through rows 1 to 12 pass at phone
and desktop sizes in both themes. Two things fall short: the iOS Simulator install couldn't run,
because this Mac has no Xcode, and four pages miss their initial-JavaScript budget, which is
report-only in M6 (section 4). What remains for you is in section 7: two settings the Browser
pane can't emulate, the iOS home-screen install, an optional chat run with a real key, and a few
content calls.

The site quotes no accuracy figure, by your decision (section 5). Instead it says plainly that it
is an unofficial fan project and that a finding can be wrong.

## 1. What M6 built

### The pages

| Page | What it does |
|---|---|
| `/` | The headline "Find the corner where the lap went wrong.", a one-sentence lead, and an Ask box with three suggested questions. A hero card of real flagged corners. One flagged corner (Norris, 2026 Azerbaijan Grand Prix race, lap 16, turn 5) is explained in four scroll steps, then drawn by the real explain-corner chart with its replay. Then the fan-project note, three findings with their intervals, how it's built (with the credit), and the limits. Static, and complete with the API stopped |
| `/chat` | Questions in plain words, answered by Claude Sonnet 5.5 through the tools over the M5 chat stream. Tool steps show as they run. Each chart card sits under its answer, with "What Claude saw" (the summary the model read) and a link into the explorers. Show telemetry on a flagged corner draws it without using a question. Stop, New conversation, up to 8 questions; the conversation lives in the browser tab |
| `/mistakes` | Pick a session from the catalog, or search ("monza quali 2025" becomes find_session's fields), to see its flagged corners ranked by time lost, as a list and on a track map. Open one for that lap's traces against the driver's usual, a map and a replay. Race context (positions, safety cars, pit stops) for races and sprints, and a strip of featured corners. The URL holds the session, driver, row count and open corner, so Back and Forward work and a link from the chat opens the right corner |
| `/styles` | Teammate pairs season by season, with the style comparison and its intervals and the style map (selecting a point swaps driver B). Any two drivers, with a "Different cars" warning before the request. Example pairs, and a lap-by-lap qualifying comparison |
| `/report` | The findings: in 60 seconds, the data, the method, whether it finds mistakes, a first human check (no numbers), whether it tells drivers apart, the 2026 rule change, what else was tried, where it falls short, and the full reports. Three figures drawn by React (the detectors against the baselines, the style probes, the 2026 shift), each with intervals, a numbers table and a source link |
| `/report/<slug>` | The 15 published files of `report/`, this one included, rendered at build time, with the 7 figures at `/report/figures/<file>` |
| `/dev/charts` | Development only (404 in a production build): every chart fixture, plus a panel that remounts the charts 20 times and checks that listeners and observers return to baseline |

Every number on `/` and `/report` comes from `web/src/content/claims.ts`. Each claim carries a
quote that must appear word for word in its report, and a test fails when a quote changes or a
component hard-codes a percentage.

**The hero card.** On 2026-10-04 you replaced the hero's abstract circuit with real examples.
The card now rotates every 4.5 s through five flagged corners of 2026, starting with the landing
example. Each is drawn on its real circuit with only that corner lit, and the card has a pause
button and dots; with Reduce motion it doesn't rotate by itself. The browser reads one static
file, `web/public/snapshots/hero.json` (10,650 bytes today), which `make site-snapshots` writes
from the explain_corner answers it already saves. Like the other snapshots it is gitignored,
because the outlines come from car positions. Without it the card falls back to the abstract
circuit. This change landed after V1 to V3 had run; the final check ran the landing again with
it, on a production build (section 2), and the budget table in section 4 includes it.

### One chart codebase

- **The core** (`web/src/charts/`): compare-laps, find-mistakes, explain-corner, compare-styles,
  race-summary and the shared `dom.ts`. They have no host import and no top-level DOM access.
  Their CSS is scoped under `.re-chart` and laid out with container queries, so a chart behaves
  the same in a 480 px card as in a 480 px iframe. `web/src/mcp-app/` keeps only the MCP wiring.
- **New options**, each off by default so Claude's charts draw as before:
  - find-mistakes: a label for its button, and a marked row;
  - explain-corner: autoplay, and no page-wide "P" key, so several replays on one page don't
    share it;
  - compare-styles: selecting a point on the style map;
  - every chart: the level of its heading, so on the site a chart's heading sits under its
    card's title.
- **Smooth curves.** Speed, throttle, time-delta and position lines, and the usual-range bands,
  are monotone cubic curves: the same curve as d3's `curveMonotoneX`, which never overshoots its
  samples. Gear, the brake strips, the maps and the track outlines are unchanged. This applies
  inside Claude too.
- **The React layer** (`web/src/components/charts/`):
  - `useChartRoot` draws in an effect and redraws once per width change. It keeps the reader's
    choices (an open table, a filter, the replay position, kind and plane) across redraws, and
    cleans up on unmount.
  - One lazily loaded wrapper per chart, and a dispatcher by bundle name for the chat.
  - `ChartCard`, with a skeleton sized like the drawn chart and a text version under it.
  - The charts follow the site's theme through the token names Claude's host sets, so a theme
    change needs no redraw.

### Talking to the API

- **No proxy.** The browser calls FastAPI directly over CORS. A typed client
  (`web/src/lib/api/`) covers the tool calls and the catalog routes, and checks each chart's
  payload with that chart's own guard before drawing.
- **The resource store** shares identical requests, cancels superseded ones, keeps the old chart
  dimmed while the next one loads, and never caches an error. Health is retried after 1, 2 and
  4 s before a page says the API is down.
- **States.** The pages that call the API have loading, empty, error, "API down" and "chat off"
  states, each naming something that still works. `/` and `/report` never show an API banner.
- **The chat client** (`web/src/lib/chat/`):
  - an SSE parser and a pure reducer, which drops the text streamed before a `retry` or
    `refusal`, as [m5_backend.md](m5_backend.md) section 7 asks, and can replay a recorded
    event list (for M7's saved answers);
  - a store kept in `sessionStorage`;
  - explorer links built from the payload, never from the model's text.
- **Nothing sends a question without a click.** The landing's Ask box hands its question to
  `/chat`, which sends it once. `/chat?q=` only fills in the question.
- **Three catalog routes in the engine:** `GET /api/catalog/sessions`, `/api/catalog/drivers` and
  `/api/catalog/styles` (`engine/src/race_engineer/api/routes/catalog.py`, with 17 tests in
  `engine/tests/test_api_catalog.py`). They are read-only, with an ETag,
  `Cache-Control: private, max-age=300` and CORS. They aren't tools, so `TOOLS`, the MCP server,
  `PROMPT_VERSION` and the prompt cache are unchanged; a fingerprint of each against the code
  before M6 matched.

### The report pages

`/report/<slug>` renders the files in `report/` at build time with an in-house parser for the
Markdown the reports use (`web/src/lib/report/markdown.ts`). It returns plain data that renders
as React elements, never HTML, and lists any line outside that subset; a test fails on any such
line in a published file. The manifest (`web/src/lib/report/manifest.ts`) lists what is
published, grouped as `/report` shows it. A test fails if a file in `report/` is in neither that
list nor the excluded list, so M7's technical report and model card can't be forgotten.
`m4_review.md` and `m3_ablations_smoke.md` are excluded, and `m4_summary.md` is published without
its human-checked accuracy (section 5).

### Design and brand

- **The look.** It is the dark pit-wall glass you chose: a navy page, floating glass layers and a
  soft ambient light, with a cool grey-blue light theme. Glass has fallbacks for Reduce
  transparency and forced colours. Type contrast does the emphasis: a sans display headline with
  a serif italic accent, serif section headlines, and mono for results and data. Fonts are system
  stacks, so the build downloads nothing.
- **Hue rules.** Lime marks the one primary action per view (Ask, Send) and the logo dot, and
  lime-family strokes are the focus rings. Pink is only time lost. Amber is only caution (the
  fan-project note, warnings, the development banner). Blue and orange appear only inside charts
  and figures, where they mean driver A and driver B.
- **Themes.** Dark is the default on a first visit; Light and System are in the header. The
  choice is applied before first paint, so there is no flash. `web/src/styles/tokens.test.ts`
  checks WCAG contrast in both themes for every text colour, control outline and focus ring
  against the surfaces it sits on.
- **Motion.** The hero draws, the sections fade up and the story steps cross-fade. None of it
  runs with Reduce motion. The replay plays once by itself only on `/` and `/mistakes`, and never
  with Reduce motion.
- **Brand.** The corner mark is a navy tile with a racing line and a lime apex dot. It is the
  favicon, the home-screen and manifest icon, and M8's app icon. On the site the logo is the
  glowing lime apex dot alone: in the header, the footer and the chat's "Race engineer" label.

### The installable app

`web/src/app/manifest.ts` gives the name "Race Engineer AI", the short name "Race Engineer",
`start_url` `/`, standalone display, and the brand navy `#070a12` as background and theme colour.
The icons are drawn from the mark: 192 and 512 px, a maskable 512 px with the mark inside the
safe zone, and a 180 px Apple touch icon. The page head carries the iOS web-app title and a
black-translucent status bar, and the header clears the safe areas of a standalone iPhone app.
There is no service worker, so the app doesn't work offline.

### Tests and scripts

- **`npm test`** runs Node's built-in test runner on 40 test files, with no test package. Among
  them: chart purity and fixtures, the API client and store, the SSE parser split at every byte,
  the chat reducer, both explorers' URL codecs, the claims and featured corners against the
  reports, the contrast table, and the report tests, which parse every published file. CI now
  runs it after the typecheck.
- **`make site-smoke`** (`web/scripts/site-smoke.ts`) checks a running site and API end to end,
  using the site's own client, guards, SSE parser and content files:
  - every page and report answers with its h1, and every internal link and anchor resolves;
  - no page shows a figure from the human review;
  - the manifest and icons resolve, and the hero card's snapshot, when there is one, holds every
    example;
  - the catalog routes and every featured corner and style example answer;
  - the chat's example questions stream valid events and call their tools, and the refusal,
    tampered-history and question-limit paths behave;
  - CORS allows both development origins and refuses others.

  It prints a table, exits with 1 on any failure and writes nothing.
- **`make site-snapshots`** (`web/scripts/snapshots.ts`) saves the featured corners from a
  running API to `web/public/snapshots/` (gitignored), so the landing draws its example and the
  hero card without calling the API.
- **`.claude/launch.json`** has `web`, `web-prod`, `api` and `api-fake`. `api-fake` has a fixed
  development chat secret, so a restarted API still accepts the histories it signed.

## 2. Checks run for this report

V1 to V4 are the four verification steps, run on 2026-10-04 against the shared `api-fake` and
`web` servers (V3 also against `web-prod`). A final independent check then re-ran the checks
below with the hero card and this report in the tree, and swept every page for the review's
figures.

| Check | Result |
|---|---|
| `npm run lint`, `npm run typecheck`, `npm test` | exit 0; 575 tests in 40 files pass in V4 and in the final check, with this report in the manifest and the hero's tests in the tree (554 in each of V1 to V3) |
| `make check` (ruff, eslint, pyright, `tsc`, pytest) | passes: 832 passed and 1 skipped in V1, 833 passed in the final check |
| `make mcp-app` | 6 bundles, byte-identical to the ones built after the last chart change (V4, and again in the final check); 0.65 to 1.57 percent larger than before M6 (241,841 to 274,806 bytes), inside the plan's 3 percent |
| `make mcp-smoke` (stdio, time targets enforced) | 0 problems: 8 tools, 6 chart bundles served, 11 calls ok (V4 and the final check) |
| `make site-smoke` against `next dev` | 59 of 59 ok (V1; again in V3); 60 of 60 in V4, with this report published (its page adds a check); 61 of 61 in the final check, which also checks the hero card's snapshot (a skip when there is none, as a run with the file moved aside showed) |
| `make site-smoke` against the production build | 59 of 59 ok, after a false failure in the script was fixed (V3, section 6); 61 of 61 in the final check |
| `make site-smoke` with the API port dead | 19 failures, 3 skips, exit 1: the script does catch a dead API (V1) |
| `npm run build` | 36 static pages in the final check (35 in V3, before this report joined the manifest), every route static or prerendered, none dynamic; `/dev/charts`, `/report/m4-review` and `/nope` answer 404 |
| The review's figures, every page (final check) | every route, all 15 report pages, the two 404s, each featured corner opened on `/mistakes`, the style examples and the seven scripted chat answers, rendered in headless Chrome (text with every disclosure open, labels, titles) and as raw HTML, under `next dev` and the production build: none shows a precision, a share or a count of judged findings from the human review, or a per-corner verdict. "Precision" appears only as the model evaluation's average precision, and review wording only where a page says it quotes no accuracy, plus the M4 reports' development checks (section 7, item 6) |
| Click-through rows 1 to 12 | pass, with rows 1, 3 and 7 amended for the decisions in section 5 (table below) |
| Every page in the click-through | 590 checks pass: no console error, no unexpected 4xx or 5xx, no sideways scroll, one h1, no skipped heading level (V2) |
| `/mistakes` on all 263 sessions | the flagged-corner chart draws in 263 of 263, with 0 console errors and 0 bad responses; 24 sessions show the empty state, and the API confirms none of their corners was flagged (V2) |
| `explain_corner` on 30 sessions | 30 of 30 draw: six per season, 2022 to 2026, covering Q, R, S and SQ (V2) |
| Parity of the split charts | the split itself: 230 of 230 exact pixel comparisons identical before and after, also with Tailwind's preflight, plus 18 of 18 focus states; the site's charts against the MCP dev host: 48 of 48 identical element by element; the heading levels: 68 dev-host runs with no difference but an intended class name (step checks) |
| Screenshots | 78, every page and state at 375 and 1280 px in both themes, kept outside the repository (V2) |

**The click-through** (plan 10.4), in headless Chrome over the DevTools protocol at 375 × 812
light and 1280 × 800 dark, with spot checks at 360 × 740. The Browser pane served page loads,
console and network checks; it pauses animation frames while hidden, so it couldn't run the
frame-dependent rows.

| Row | Page | Result |
|---|---|---|
| 1 | `/` | Pass. The first screen holds the h1, lead, Ask box and chips (the chips end at 677 of 740 px at 360 px wide, 650 of 812 at 375, 707 of 800 at 1280). The example loads from its snapshot; with the snapshots moved aside, live after a deliberate 404; with the API refused too, as a text card. The fan-project note and the three findings are there, with no accuracy figure and no reviewer wording, and the credit names Yuchun Wu with GitHub and LinkedIn, no email. With the hero card (final check, production build, at 360, 375 and 1280 px in both themes): five real examples starting on Norris; it turns by itself after about 4.6 s, holds while paused, hovered or off screen (at 360 × 740 it starts below the fold and turns once scrolled into view), and a dot picks an example; with Reduce motion it neither turns nor draws in; a late `hero.json` moves nothing; without it the abstract circuit shows. No console error, no 4xx, no sideways scroll, layout shift 0 |
| theme | every page | Pass, 24 of 24 checks per size. A fresh profile is dark before first paint. Light, System and Dark apply at once and survive a reload with no flash, under a dark and a light system setting. Junk in storage gives dark |
| 2 | `/chat` | Pass. The Monaco chip sends exactly one request, and the race-summary card sits under the text with "What Claude saw". Show telemetry makes one `explain_corner` call and no chat request, and the question meter doesn't move. "Open in Mistakes" lands on the session, and Back keeps the conversation |
| 3 | `/mistakes` | Pass. The defaults are the 2026 Azerbaijan race, 10 rows, 4 API calls. Norris's featured card opens with focus on its heading, the traces, the map and the replay, which plays once and stops. "P" does nothing; Space plays and pauses. Back closes the panel and Forward reopens it. Race context draws. "monza quali 2025" filtered to LEC says no corner stood out among the 66 scored, with the next step. The featured strip has the eight corners listed in section 5 |
| 4 | `/mistakes` | Pass. `?year=1999&session=X&explain=bad` loads the defaults, with no 4xx |
| 5 | keyboard | Pass. Tab reaches the pickers, search, cards, map and Show telemetry, each with a focus ring; Enter focuses the panel heading. The phone's session sheet and menu keep focus inside, and Esc returns it to their buttons |
| 6 | `/styles` | Pass. The default is HAM and LEC, 2025, with the map. Kind and plane update the URL without a history entry, keep focus and survive a reload. A pair from the list works; VER and NOR warn with no request made. A map point swaps driver B. NOR and PIA at Abu Dhabi 2025 draw compare-laps |
| 7 | `/report` | Pass. The figures toggle and have numbers tables; 44 internal links resolve, anchors included. The report figures sit on white cards in both themes. `/report/m4-review` is a 404. At 360 px the tables of m3-shift and m4-mistakes scroll inside their frames (m4-summary shows none: its one table is in the omitted section) |
| 8 | `/chat` | Pass. `#refuse`, `#truncate`, `#busy` and `#overloaded` (Try again resends once and replaces the failed turn) behave; the text before a retry is dropped; a ninth question sends nothing. New conversation asks first. Stop shows "Stopped." and leaves the meter alone. A reload restores the conversation |
| 9 | `/chat` | Pass. After `api-fake` was stopped and started again, the next question was answered with its chart. One changed character in the stored signature gives a 400 and the "can't continue" block, and the transcript stays |
| 10 | API down | Pass. `/` and `/report` make no API request and show no notice. `/mistakes` and `/styles` show the API-down notice in place within about 0.1 s, and the banner about 7 s later, after the health retries; `/chat` says "Connecting…", then shows the notice with Send disabled. With the API back, Try again recovers all three |
| 11 | `/dev/charts` | Pass. 92 of 92 render checks. With three copies each of compare-laps and explain-corner, no clip id repeats, one replay plays alone and "P" toggles none. The lifecycle counters return to baseline after 20 remounts |
| 12 | production | Pass. Rows 1, 3, 6 and 7 at 375 light and 1280 dark, with 0 failures, clean consoles and no 4xx (V3; row 1 again with the hero card in the final check). The manifest is served as `application/manifest+json`; the icons have the right sizes, are fully opaque, and the maskable mark sits inside the safe zone |
| — | colours | Pass, every page in both themes. Lime appears only on the logo dot, Ask, Send, the phone's Chat pill and the story's step number; pink only on time lost; amber only on the fan-project note and the development banner; blue and orange only in charts, figures and the style mini bars |

## 3. Timings

| Item | Value |
|---|---|
| `/mistakes` defaults, warm | the list drawn 486 to 523 ms after navigation under `next dev`; the page fully loaded in 493 ms at 1280 px and 535 ms at 375 px in the production build |
| All 263 sessions, page load to the drawn list (`next dev`) | median 515 ms, 90th percentile 545 ms, slowest 885 ms (2022 Mexico City Grand Prix race) |
| The `find_mistakes` call in that sweep | median 63 ms, slowest 190 ms |
| 30 sessions, page load to the drawn corner | 625 to 1,104 ms; the `explain_corner` call 181 to 576 ms |
| Catalog routes, warm (median) | sessions 3.7 ms, drivers 4.4 ms, styles 3.2 ms; 1.4 to 1.7 ms for a 304 |
| Catalog routes, first call in a fresh process | sessions 262 ms, drivers 180 ms, styles 77 ms |
| `make mcp-smoke`, first call of each kind | `explain_corner` 0.40 s cold, `find_session` 0.30 s, every other call 0.10 s or less |
| API down: notice in place, then banner | about 0.1 s, then about 7.1 s |

## 4. Payload sizes and budgets

**Payloads** (V1: the whole REST answer, summary included, as the site receives it):

| Call | Size |
|---|---|
| Session catalog | 31.9 KB |
| `explain_corner`, the featured corners | 19.7 to 29.3 KB |
| `find_mistakes`, limit 50, the default session | 14.1 KB (10 rows: that race has 10 distinct mistakes) |
| `compare_laps`, NOR and PIA, 2025 Abu Dhabi qualifying | 48.1 KB |
| `compare_driving_styles`, the style examples | 25.7 to 30.6 KB |
| `find_mistakes` for LEC, 2025 Italian qualifying (empty list) | 6.4 KB |

**Budgets** (plan 10.6, report-only in M6), measured on the production build by V3, then again
in the final check with the hero card and this report in the build: only `/` changed (181.8 KB in
V3). KB is 1,024 bytes of gzip at level 9.

| Budget | Limit | Measured | Result |
|---|---|---|---|
| Initial JS, `/` | 160 KB | 185.8 KB (the hero card adds 4.0 KB) | over |
| Initial JS, `/report` | 160 KB | 151.1 KB | within |
| Initial JS, each `/report/<slug>` and the 404 page | 160 KB | 139.1 KB | within |
| Initial JS, `/chat` | 190 KB | 194.3 KB | over |
| Initial JS, `/mistakes` | 190 KB | 190.7 KB | over |
| Initial JS, `/styles` | 190 KB | 191.5 KB | over |
| One chart chunk, wrapper and core | 30 KB (explain-corner 40 KB) | 12 KB at most (explain-corner: core 10.0 KB, wrapper 1.5 KB) | within |
| API calls on an explorer's first load | 4 | `/mistakes` 4, `/styles` 3, `/chat` 1 | within |
| Layout shift when a chart arrives | none visible | CLS 0 everywhere; every drawn height within 10 percent of its skeleton | within |

Not counted as initial JS: the stylesheet (18.2 KB on every page; 17.9 KB before the hero
card), the noModule polyfill (38.5 KB, which modern browsers skip) and the other routes' chunks
that Next prefetches after load. Of each page's total, 139.1 KB is shared by every page. The
command, run against `next start` on port 3000, sums the gzip size of every `<script src>` chunk
each prerendered page references:

```js
// node budget-js.mjs http://localhost:3000   (plan 10.6; scratch tooling, not in the repository)
// SITE is the server; slugs are the report slugs in the build (15 in the final check)
const gz = (buf) => zlib.gzipSync(buf, { level: 9 }).length;
for (const page of ["/", "/chat", "/mistakes", "/styles", "/report", ...slugs.map((s) => `/report/${s}`), "/nope"]) {
  const html = await (await fetch(SITE + page)).text();
  const srcs = [...html.matchAll(/<script\b[^>]*\bsrc="([^"]+)"[^>]*>/g)]
    .filter((m) => !/\bnoModule\b/i.test(m[0])).map((m) => m[1]);
  let total = 0;
  for (const src of srcs) total += gz(Buffer.from(await (await fetch(SITE + src)).arrayBuffer()));
  console.log(page, (total / 1024).toFixed(1), "KB");
}
```

**Why four pages are over.** `web/src/lib/api/client.ts` imports the five charts' payload guards
from the chart core modules, and Turbopack doesn't drop the unused rest of a module. So every
page that uses the API client ships all five chart cores, about 28 KB, even though the chart
wrappers themselves load lazily. `/` also pulls in explain-corner through its story
(`components/landing/story.ts` and `featured-corner.ts`). Moving the guards into small modules of
their own, or having `callTool` import a guard only when it needs it, would bring the explorers
to about 163 to 166 KB and `/` to about 165 KB, or about 155 KB if `anomalyStretch` and
`isCornerChart` move too (V3's estimates, plus the hero card's 4 KB). It wasn't done in V: the
change is in the API client and the chart core that the MCP bundles share, and the budgets are
report-only until M7 enforces them.

**Layout shift**, with each API answer held back 2.5 s to measure the skeleton (height in px,
skeleton then drawn):

| Chart | 1280 dark | 375 light |
|---|---|---|
| `/` example corner | 940, then 948 | 1310, then 1300 |
| `/mistakes` list | 1410, then 1436 | 1410, then 1505 (+6.7 percent) |
| `/mistakes` corner panel | 940, then 915 | 1310, then 1300 |
| `/mistakes` race context | 535, then 548 | 575, then 580 |
| `/styles` pair | 1120, then 1121 | 1400, then 1388 |
| `/styles` lap by lap | 520, then 493 (−5.2 percent) | 575, then 562 |

The chat's chart cards also showed no layout shift at either size.

## 5. Decisions

### Yours

1. **Credit:** Yuchun Wu, with GitHub and LinkedIn and no email, in the footer and in "How it's
   built" (`web/src/content/site.ts`).
2. **No reviewer verdicts.** The featured corners show no per-corner verdict, no "checked on
   video" badge and no reviewer notes, and the landing caption doesn't say a reviewer checked
   its corner. The corners are picked by the tool's own ranking and the mistake type.
3. **No accuracy figure for now.** One reviewer and 50 findings is too small a sample to
   headline, and a larger review is planned. So the landing has no trust card. In its place is
   a short note, "A fan project, so it can be wrong", which tells the reader to treat a finding
   as a lead to check against the replay, not a verdict. The limits say the findings haven't
   been checked at scale yet. `/report` has no accuracy card or chart, and its "A first human
   check" section is one paragraph without numbers. The footer says "A fan project: findings can
   be wrong." `report/m4_review.md` isn't published.
4. **Headline:** "Find the corner where the lap went wrong."
5. **Visual direction:** the pit-wall style with some hue and bright colour, floating
   liquid-glass layers, dark edge to edge, a cool grey-blue light theme, and type contrast to
   carry the findings. The design spec written from it bound every page step.
6. **Brand and logo:** the corner mark is the favicon and app icon. After the build you made the
   site's logo the glowing lime dot alone, in the same lime on both themes.
7. **Lime buttons:** lime with near-black text in both themes, for the one primary action per
   view.
8. **Smooth curves** in the charts, on the site and inside Claude (section 1).
9. **An installable home-screen app** in M6, without a service worker, and an M8 row (a native
   iOS app on the deployed API) in the README's milestone table.
10. **A hero card of real, rotating examples** instead of the abstract circuit (section 1).

### The orchestrator's (reversible)

1. **`/report/m4-summary` without the accuracy.** `report/m4_summary.md` still quotes the
   human-checked precision. Rather than edit the M4 report, the manifest leaves out its
   "Human-checked precision" section and two sentences elsewhere that refer to its result. A
   muted note stands in its place: "A first human check of the 50 top findings is in the
   repository's copy of this report. The site doesn't quote an accuracy from it yet: one
   reviewer and 50 findings is too small a sample, and a larger review is planned." A test fails
   if an omitted heading or sentence stops appearing word for word in the file, and another if
   any report page quotes a percentage with an interval.
2. **`featured.ts` without verdicts.** An entry's kind is flagged, energy, saved time or nothing
   flagged; the verdict, video and review-rank fields are gone. `featured.test.ts` checks each
   flagged corner against `report/m4_review_queue.csv`, which has no verdict column, and never
   reads the labels. What changed in the list is below.
3. **The site's `.panel` class became `.glass-panel`.** It leaked into explain-corner's and
   compare-styles' own `.panel` on the site; a test now fails on any class name the site and
   the charts share.
4. **Dark is the default theme** on a first visit, as the design spec says: the identity was
   tuned on navy, and Light and System are one tap away.
5. **Light-theme contrast adjustments,** so both themes pass WCAG AA on the glass and the lit
   page: muted text `#56627a` to `#4d5970`, pink `#d6315b` to `#c42a52`, amber `#a86b00` to
   `#8f5b00`, a darker lime `#3a6209` for small lime-family text, and control outlines at 0.62
   opacity in light (0.46 in dark), so they reach 3:1 against both their fill and the page.
   Faint grey is never used for text, and the header's text is always the main ink colour.

### Featured corners

The plan listed nine featured entries. Eight remain, and all eight answered live in
`make site-smoke`. None of the reserve corners was needed. One planned entry was dropped and one
planned blurb rewritten, because they drew on the human review, which the site doesn't show
corner by corner (decision 2); every blurb now uses the explanation's own words.

The eight: Norris at Baku (the landing example: the tool lists it first among that race's flagged
corners, of the over-slowing type, on a lap that wasn't looked at while the rules were built),
Albon in Madrid, Sainz in Melbourne, Hülkenberg at Monza's first chicane, Sainz at Silverstone,
Antonelli lifting at Monza (likely energy management), Russell's chicane cut at Monaco (it saved
time) and Leclerc's 2025 Monza qualifying (nothing flagged among 66 scored corners).

## 6. Defects found during verification

**Fixed:**
- The README asked for Node.js 20 or later, but the web tests and both scripts need Node's own
  type stripping. It now says 22.18 or later (CI uses 24), and so does `web/README.md` (V1).
- The README's M5 row was out of date (V1).
- `web/scripts/snapshots.ts` pointed people to `make site-snapshots`, which didn't exist; it
  does now, next to `make site-smoke` (V1).
- CI didn't run the unit tests; `npm test` now runs after the typecheck (V1).
- `make site-smoke` failed falsely against the production build: there `/mistakes` is a static
  shell whose featured strip appears only after hydration, so `/mistakes#featured` had no id in
  the HTML. The anchor check now skips that id on a production build only. In headless Chrome a
  direct load of `/mistakes#featured` and the landing's link both land on the strip (V3).
- This report, which the site publishes, named the featured corner that was dropped and the one
  whose blurb was rewritten, with the human review as the reason. That hinted at a verdict on
  each, so section 5 now says what changed without naming them (final check).
- The README's and `web/README.md`'s landing rows didn't mention the hero card, and
  `make site-snapshots` was described without `hero.json` (final check).
- `make site-smoke` expected the slug count to grow once this report was listed, with a branch
  for either case; it now expects 15. It also checks the hero card's snapshot: every example
  drawable, the first the landing corner, and a skip when the file isn't there (final check).

**Not fixed:** the chart cores load with every page that uses the API client, so four pages miss
their initial-JS budget (section 4).

**Not a defect:** under `next dev`, `/chat`'s raw HTML has two h1s, the Suspense fallback and the
streamed content, which React swaps. The page shows one. At 360 × 740 the hero card starts below
the fold (its top at 823 px), so it doesn't turn until it is scrolled into view, as designed.

The step checks before V fixed their own defects. One also affected Claude's charts:
find-mistakes failed to draw when a driver gained time at a corner (a negative time lost made the
map's marker size NaN), on 24 of the 263 sessions. Two more were in the shared chart code but
showed only on the site: a real mouse click on the style map never selected a driver, and the
site's `.panel` class leaked into two charts.

## 7. What remains for you

1. **Reduce motion and Reduce transparency.** Turn on each in macOS settings (Accessibility,
   Display) and open `/` and a featured corner on `/mistakes`. The hero card and the replay
   shouldn't move by themselves, and glass should become solid. The Browser pane can't emulate
   either setting.
2. **Optional: a chat run through the site with a real key.** Start `make api` from a terminal
   where `ANTHROPIC_BASE_URL` is unset or `https://api.anthropic.com`, with a key, plus
   `make web`. Then ask the chips on `/chat`: about 6 questions, roughly $0.30. It is also the
   first time the site's own store holds a real history with thinking blocks.
3. **Optional: a real phone over the LAN.** This needs the Mac's hostname in
   `allowedDevOrigins` and in `RACE_ENGINEER_CORS_ORIGINS`, and `NEXT_PUBLIC_API_URL` pointing at
   the Mac.
4. **The home-screen install on iOS.** On a phone, or in the iOS Simulator once Xcode is
   installed (free from the Mac App Store, then
   `sudo xcode-select -s /Applications/Xcode.app/Contents/Developer`): Add to Home Screen, the
   full-screen launch and the light theme's status-bar strip. Next 16 writes
   `mobile-web-app-capable`, not the older Apple tag, so only a real install shows whether iOS
   opens the app full screen.
5. **The look of the charts inside Claude.** The smooth curves and the find-mistakes fix changed
   the MCP bundles. `make mcp-smoke` passes, but nobody has looked at them in the Claude desktop
   app since.
6. **The m4-summary omission, and the review wording left in two reports.** `/report/m4-summary`
   still has "against the human verdicts" (under Next) and "The human check is one reviewer and
   50 findings." (under Limits), and its third finding reports Claude's pre-read. `m4_mistakes.md`
   shows Claude's pre-read per flag, labelled "not an evaluation", and a table from the
   development review of the traffic rules. None is a per-corner human verdict or an accuracy
   figure. Removing them is a content call; each would need an omission of its own and a note.
7. **`report/m4_review_labels.json` before the repository goes public.** The site never reads
   it, but it holds the reviewer's verdict for each finding, as `report/m4_review.md` does.
   Decide whether they stay in the public repository.
8. **The budgets** (section 4): fix the eager chart cores now or in M7.
9. **The API-down banner** on `/mistakes` and `/styles` appears about 7 s after the in-place
   notice, once the health retries end. That is by design; say if you want it sooner.
10. **A read of the site's copy.** The plan's done-when for honest numbers has two halves: the
    claims and featured-corner tests, which pass, and your review of the words around them: the
    landing (hero, story, fan-project note, the three findings, limits), the `/report` sections
    and the featured blurbs.

## 8. What changes in M7

From the plan's section 12, with what V adds:

| M7 item | What M6 already does | What M7 adds |
|---|---|---|
| Rate limits (10 an hour, 25 a day) | Unknown error codes show the server's message; the chat reducer has no fixed list of codes | A 429 `rate_limited` before the stream. `Retry-After` isn't readable over CORS, so put `retry_after_s` in the error body or expose the header |
| The $3 a day cap and saved example answers | `replay(events)`; stable question ids with a `saved` slot; the chat-off state already links elsewhere | Record each answer's events per question id and show them, labelled with their date, when capped or off |
| Deploying the site (Vercel) | Static routes; `NEXT_PUBLIC_API_URL`; no network at build time; the reports read with `fs` at build | `web` as the project root with `../report` included; decide whether `web/public/snapshots/*.json` and `hero.json` are committed or written at deploy from the deployed API (they hold outlines drawn from car positions) |
| Deploying the API | Direct CORS; histories survive restarts with a fixed secret | `RACE_ENGINEER_CORS_ORIGINS` set to the site's origin (plus previews); `RACE_ENGINEER_CHAT_SECRET` set; a request-size cap at the proxy; no gzip on `text/event-stream`; a short comment at the stream's start if Safari holds back the first events |
| CSP | One constant inline script (the theme) | `script-src 'self'` plus its hash, and `connect-src` set to the API's origin |
| The repository goes public | `NEXT_PUBLIC_REPO_URL` turns report references into links and shows the header's source link | Set it; the README's report links can point at the site once it is deployed |
| Technical report and model card | The manifest's "every report file is listed" test; `/report` reserves the link | Add both to the manifest and link them from `/report` and the header |
| Sharing | A title and description per page | An Open Graph image, `robots.ts` and `sitemap.ts` |
| Budgets and end-to-end tests | Budgets measured (section 4); the click-through; `make site-smoke` | Fix the eager chart cores, then enforce the budgets in CI; optionally Playwright (a browser download) |
| Real devices | Checked in Chromium only | Safari's handling of the chat stream; the iOS home-screen install |

## 9. Known limits and notes for later

- `/mistakes` and `/styles` ignore bad URL keys and never send them, but leave them in the
  address bar until the next change.
- A `/mistakes` link naming an event the data doesn't have gets the tool's 422, shown unchanged,
  including its line for the model ("Call list_sessions…"). The page sends a full link at once,
  without waiting for the session list, so it can't also rule this out.
- On `/styles`, a season outside the style data makes two 422 requests (the list and the tool,
  in parallel) before the page drops it. A `laps` weekend that isn't in the season's list shows
  the list's last weekend above an error about the URL's.
- At 320 px some charts draw a little under their 300 px floor; they shrink to fit rather than
  scroll, as the plan accepted.
- Before any data arrives, the flagged-corner skeleton reserves room for 10 rows, so a short list
  (a qualifying session, or one driver) is shorter than its skeleton when it lands.
- With the chat back on after a reload, a turn that failed with "chat disabled" still shows that
  message in the history.
