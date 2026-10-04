# race-engineer (web)

The website of Race Engineer AI, and the charts that show inside Claude. See the
[project README](../README.md) for the overview, and [docs/DEPLOY.md](../docs/DEPLOY.md) for
deploying it on Vercel.

The site lets a visitor ask about any F1 qualifying, sprint or race since 2022 in plain words,
browse the corners the model flagged race by race, compare how teammates drive, and read what
the model found and where it falls short. It is a Next.js 16 app (App Router, React 19,
Tailwind 4) that calls the project's FastAPI server directly from the browser, over CORS, with
no Next.js proxy. Every route is static: the report pages are built from `../report` at build
time, and the explorers and the chat fetch their data in the browser, so a page still reads
when the API is down. The site is an unofficial fan project; it quotes no accuracy figure and
says that its findings can be wrong.

This is not the Next.js you may know: read `AGENTS.md` (the docs that ship in
`node_modules/next/dist/docs/`) before changing Next.js code.

## Run it

From the repository root, start the API and the site in two terminals:

```bash
make api-fake   # the API at http://127.0.0.1:8000, with the scripted chat (no API key needed)
make web        # the site at http://localhost:3000 (npm run dev)
```

`make api` instead of `make api-fake` runs the chat on Claude with your own key (see "The real
chat" in the project README's Quick start). In the Claude desktop app's Browser pane,
`.claude/launch.json` has the same servers: `api-fake` (or `api`) and `web`, plus a production
build served on the same port (`web-prod`), and the production-like pair below (`api-prodlike`,
with variants for other hourly limits, a $0.01 cap, a pause and the chat off, and
`web-prodlike`).

**The production-like pair,** for checking what a deployment does without deploying:
`make api-prodlike` serves the served-data bundle (`make serve-data`) on port 8001 as a public
host would, with the limits on (in memory) and stateless MCP, and `make web-prodlike` builds the
site against it and serves it on port 3001, with the security headers. `next build` and
`next dev` each lock the project, so stop `make web` first.

Three settings, all optional and inlined at build time (copy `.env.example` to `.env.local`; on
Vercel, set them in the project's Environment Variables):

| Variable | Default | What |
|---|---|---|
| `NEXT_PUBLIC_API_URL` | `http://127.0.0.1:8000` | the API the browser calls. A site on another origin than `localhost:3000` or `127.0.0.1:3000` also needs that origin in the API's `RACE_ENGINEER_CORS_ORIGINS`. A Vercel production build fails without an `https://` value |
| `NEXT_PUBLIC_REPO_URL` | empty | the public repository: report references become links, and the header shows a source link |
| `NEXT_PUBLIC_SITE_URL` | Vercel's production domain, else `http://localhost:3000` | the site's own address, for the sharing image tags, `robots.txt` and the sitemap; only for a custom domain |

| Page | What |
|---|---|
| `/` | What it is, an Ask box, a rotating card of real flagged corners on their circuits (from `public/snapshots/hero.json`, no API call), one of them explained step by step and drawn by the real chart, the fan-project note, three findings with their intervals, how it's built, and its limits |
| `/chat` | Questions in plain words, answered by Claude through the analysis tools, with the charts under each answer and "What Claude saw" (the tool summary the model read). The conversation lives in the tab (`sessionStorage`); the server keeps none. When the chat is limited, paused or off, saved answers stand in (below) |
| `/mistakes` | Pick a session, see its flagged corners ranked by time lost, open one for that lap's telemetry against the driver's usual, with a replay. The selection is in the URL |
| `/styles` | Teammate pairs by season: the style differences with their intervals, the style map, a custom pair, and a lap-by-lap head to head |
| `/report` | The curated findings: data, method, the detector comparison, the style probes, the 2026 rule change, the limits; then the technical report, the model card and every published report |
| `/report/<slug>` | Every published report file in `../report`, listed in `src/lib/report/manifest.ts` |
| `/dev/charts` | Development only (404 in a build): every saved tool result drawn through the site's chart wrappers, with lifecycle counters |

The theme is dark on a first visit, with Light and System in the header. The layout works from
a 360 px phone up, and the site installs to a phone's home screen (`app/manifest.ts`, icons from
`app/pwa-icon/`; no service worker, so nothing works offline). A shared link shows a 1200 × 630
image drawn at build time (`app/opengraph-image.tsx`, `twitter-image.tsx`): the logo's glowing
dot, the name, the headline, on the dark page. `app/robots.ts` and `app/sitemap.ts` write
`/robots.txt` and `/sitemap.xml` (the five pages and every published report).

## Folders

| Folder | What's in it |
|---|---|
| `src/app/` | Routes: `(site)` (landing, explorers, report, with the footer), `(app)/chat` (full height), `(dev)/dev/charts`, plus the root layout, theme script, manifest, icons, the sharing image, robots and sitemap |
| `src/charts/` | The chart core shared with the MCP App (below), with its CSS in `styles/` |
| `src/mcp-app/` | The MCP App wiring, one page per chart, and the dev host with its sample results |
| `src/components/` | `layout/` (header, menu, theme toggle, footer), `ui/` (buttons, fields, notices), `status/` (API states), `charts/` (the React wrappers), one folder per page (`landing/`, `chat/`, `mistakes/`, `styles/`, `report/`, `findings/`), `brand/` and `motion/` |
| `src/lib/` | Pure modules: `api/` (the typed client, the resource store, health, catalog, chat status and tool hooks), `chat/` (event types, the SSE parser, the client, the reducer, the store, persistence, saved answers, links), `report/` (the manifest, a Markdown subset parser, links, PNG sizes, the omitted sections, the number trace), plus `env`, `csp`, `site-url`, `budgets`, `theme`, `url` and `format` |
| `src/content/` | The site's words and picks: `claims.ts` (every number, each with a verbatim quote from `../report`), `featured.ts` (the example corners), `questions.ts` (the chat's example questions), `style-examples.ts`, `site.ts` |
| `src/styles/` | The design tokens, under the names the charts read (`--color-text-primary`, ...) |
| `scripts/` | Build and check scripts run with Node (below); `demo/` films the demo video ([docs/demo.md](../docs/demo.md)) |

## Scripts

| Command | What |
|---|---|
| `npm run dev` | The site at http://localhost:3000 (`make web`) |
| `npm run build`, `npm start` | A production build, and serving it |
| `npm run build:vercel` | Vercel's build (`vercel.json`): `check-env.ts`, then `snapshots.ts --tolerant`, then `saved.ts pull --tolerant`, then `next build` (below) |
| `npm run lint`, `npm run typecheck` | ESLint, and `next typegen` then `tsc` |
| `npm test` | The unit tests (`make web-test`) |
| `npm run budgets` | After a build: each page's initial JavaScript against its budget (`make budgets`; below) |
| `npm run smoke` | `scripts/site-smoke.ts` (`make site-smoke`): checks the running site and API end to end. Options `--site` (default http://localhost:3000), `--api` (default http://127.0.0.1:8000), `--origin` (the one browser origin a deployed API allows) and `--chat fake`, `one` or `skip` (below) |
| `node scripts/ci-smoke.ts --phase rate\|cap` | CI's end-to-end check: a production build against an API on made-up data (`engine/scripts/synthetic_tree.py`), with the limits on |
| `node scripts/snapshots.ts` | Saves the featured corners from a running API to `public/snapshots/` (gitignored), plus `hero.json`, what the landing's card of real corners draws, so the landing page needs no API call (`make site-snapshots`). `--tolerant` skips what fails and always exits 0 |
| `node scripts/saved.ts record --api <url>` | Records the chat answering the seven example questions into `../data/saved/` (`make saved-record API=…`); refuses the scripted chat unless `--allow-fake` |
| `node scripts/saved.ts check`, `pull --api <url>` | Validates recordings; copies the API's into `public/saved/` (gitignored) at build time |
| `node scripts/check-env.ts` | Fails a Vercel production build whose `NEXT_PUBLIC_API_URL` isn't an `https://` address |
| `node scripts/demo/record.ts --site <url> --api <url>` | The demo video (`make demo-video`; [docs/demo.md](../docs/demo.md)) |
| `npm run build:mcp-app`, `npm run dev:mcp-app` | The MCP App bundles (`make mcp-app`), and the dev host at http://localhost:5174/dev-host.html (`make mcp-app-dev`) |

## Deploying on Vercel

The project's Root Directory is `web`, with the files outside it included in the build (the
report pages read `../report`), Node 24 (`"engines"` in `package.json`), and the build command
from `vercel.json`, `npm run build:vercel`:

1. `scripts/check-env.ts` stops a production build without an `https://` `NEXT_PUBLIC_API_URL`:
   the site would otherwise ship calling `http://127.0.0.1:8000`, and every page would look
   broken.
2. `scripts/snapshots.ts --tolerant` copies the featured corners and `hero.json` from the API.
3. `scripts/saved.ts pull --tolerant` copies the saved answers from the API into `public/saved/`.
4. `next build`.

Steps 2 and 3 are server to server, so CORS doesn't apply, and a call that fails is logged and
skipped: the landing page then falls back to the live API (and to text), and the chat reads its
saved answers from the API, or offers none. Every push to `main` rebuilds the site; after new
data or new saved answers on the API, a Redeploy in Vercel's dashboard copies them. The copies
are gitignored: their charts carry positions derived from F1 data. The whole sequence, with the
API: [docs/DEPLOY.md](../docs/DEPLOY.md).

## Security headers

A production build (`next build`, then `next start` or Vercel) sends these on every route,
from `next.config.ts` and `src/lib/csp.ts`; `next dev` sends none, because hot reload needs
`eval` and a websocket:

```
Content-Security-Policy: default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline';
  img-src 'self' data:; font-src 'self'; connect-src 'self' <the API's origin>; object-src 'none'; base-uri 'self';
  form-action 'self'; frame-ancestors 'none'; manifest-src 'self'[; upgrade-insecure-requests]
X-Content-Type-Options: nosniff
Referrer-Policy: strict-origin-when-cross-origin
Permissions-Policy: camera=(), microphone=(), geolocation=(), browsing-topics=()
X-Frame-Options: DENY
```

The browser may fetch only from the site and from the API the build was made for
(`NEXT_PUBLIC_API_URL`'s origin), and `upgrade-insecure-requests` is added only when that is
https, so a local production build against `http://127.0.0.1:8000` still works. `script-src`
allows inline scripts because every static page carries Next's inline payload scripts (they
change with every build) next to the theme script: a list of hashes would block hydration, and
nonces would make every page dynamic. What keeps that safe is the site's own rule: API and chat
text reach the page only as React text, the one `dangerouslySetInnerHTML` is the constant theme
script, and report Markdown is parsed into tokens. No `Strict-Transport-Security` from the app:
Vercel sends its own on its domains.

## Saved answers and the chat's limits

The deployed chat has limits (10 questions an hour and 25 a day per connection, a daily spending
cap, a kill switch; see [engine/README.md](../engine/README.md#guardrails-race_engineerapilimits)).
On the first visit `/chat` reads `GET /api/chat/status` (`src/lib/api/chat-status.ts`), so it
knows before anyone types whether a question would be answered. When it wouldn't, a notice above
the composer says why, in the site's own words, and for how long (the hourly limit counts down
the minutes); the composer is disabled until then, and a question from the landing page's Ask box
waits in the composer instead of being sent. The meter shows the questions left today once 5 or
fewer remain.

Meanwhile the example chips become saved answers: recordings of the chat answering the same
questions earlier, with the same tools (`src/lib/chat/saved.ts`, shown by
`components/chat/SavedAnswers.tsx`, which loads only when needed so `/chat`'s initial JavaScript
stays inside its budget). They are read from the site's static copies (`/saved/index.json`,
`/saved/<id>.json`) first and from the API (`/api/saved`) second, each checked by the same guards
as a live answer. A saved answer is labelled with its date, its charts, "What Claude saw" and
"Show telemetry" work as in a live one (the tools still answer when the chat doesn't), and it
never enters the conversation sent to the server. The recordings are made with
`scripts/saved.ts record` (above); `src/lib/chat/fixtures/saved-basic.json` is a hand-written
synthetic one for the tests.

The footer and the chat's small print carry what the limits keep about a visitor
(`components/chat/privacy.ts`): a daily-changing code made from the IP address, deleted within
about a day, and no IP address, cookie or question.

## Size budgets

`npm run budgets` (`make budgets`, and CI after its build) reads each prerendered page in
`.next/server/app/`, gzips (level 9) every `<script src>` chunk it loads in a modern browser, and
sums them in KiB of 1,024 bytes, M6's measure (`report/m6_website.md`, section 4). The limits are
"no regression": M6's figure plus 5%, rounded up (`src/lib/budgets.ts`):

| Page | M6 (KiB) | Limit (KiB) |
|---|---|---|
| `/` | 185.8 | 196 |
| `/chat` | 194.3 | 205 |
| `/mistakes` | 190.7 | 201 |
| `/styles` | 191.5 | 202 |
| `/report` | 151.1 | 159 |
| `/report/<slug>` (the manifest's first) | 139.1 | 147 |

It prints the table and exits 1 when a page is over its limit or can't be measured. Run it on a
build made from the same tree (the slug it measures comes from the current manifest).

## Tests

`npm test` runs every `src/**/*.test.ts` with Node's built-in test runner: no browser, no DOM
and no test packages. Node (22.18 or later; CI uses 24) strips the types itself, so tested
modules import each other with the `.ts` extension and use no enums or namespaces. The tests
cover the API client and resource store against a fake `fetch`, the hero card's examples, the
chat's SSE parser (split at every byte), reducer, store and persistence (every limit code, the
status on first load, saved answers that survive a reload and are never sent), the saved
answers' loader and guards, the URL state of both explorers, the report manifest and Markdown
parser over every published report, the design tokens' contrast in both themes, the chart core's
purity and fixtures, the security headers, the site's address, the budgets' chunk reader, and
the content: every number in `claims.ts` must still appear, word for word, in the report it
cites, and no claim may quote a section the site leaves out; every number in the technical
report and the model card must appear in a source they link (`src/lib/report/trace.ts`); and the
landing page, the findings components and `/report` hard-code no percentage (they take every
figure from `claims.ts`).

CI's web job (`.github/workflows/ci.yml`) runs `npm ci`, `lint`, `typecheck`, `test`, the
scripts' own tests (the demo video's captions), `build`, `budgets` and `build:mcp-app`; it has
no API and no data, and needs neither. With the API and its data running locally,
`make site-smoke` checks every route and report page (each with its h1, none showing an accuracy
figure, every internal link and anchor resolving), the web app manifest and icons, the hero
card's snapshot (when there is one), the catalog routes, every featured corner and style example
through the site's own client and guards, the chat's example questions parsed with the site's
own SSE parser (with the scripted chat only), the refusal, tampered-history and question-limit
paths, and the CORS preflights from both dev origins. On a production build it also checks the
security headers, and on a deployment (`make deploy-check`) that the build's copies of
`hero.json` and the saved answers arrived; there `--chat one` asks one real question and checks
that its events arrive spread over the answer, and `--chat skip` asks nothing. It prints a
table, exits 1 on any failure and writes nothing.

CI's end-to-end job builds the site against the API serving made-up data (no F1 data in CI) and
runs `scripts/ci-smoke.ts`: every page, the security headers, robots, the sitemap and the sharing
image, a scripted question streamed through the site's own client, the third question in the hour
refused 429 with `Retry-After` readable by the page, then, after a restart with a $0 cap, the
status saying `daily_cap` and a saved answer loading.

## The chart core, shared with the MCP App

The five charts (find-mistakes, explain-corner, compare-laps, compare-styles, race-summary)
are written once, in `src/charts/<chart>.ts`: plain TypeScript that draws SVG and HTML into a
root element, with no MCP import and no top-level DOM access.
- **Inside Claude**, `src/mcp-app/<chart>.ts` wires a chart to the MCP Apps host, and Vite
  builds each into one self-contained HTML file in `engine/src/race_engineer/mcp_server/ui/`,
  which the MCP server serves (`make mcp-app`).
- **On the site**, `src/components/charts/` wraps each chart in a small React component that
  lazy-loads it and draws it through one hook, `useChartRoot`: it draws when the data changes,
  redraws only when the width does, keeps the reader's choices (an open table, a replay's
  position) across redraws, and cleans up on unmount.

The chart CSS is scoped under `.re-chart` and laid out with container queries, so a chart looks
the same in Claude's iframe and in a site card of the same width. The site defines the host's
token names (`--color-text-primary`, `--color-background-primary`, ...), so the charts follow
the site's theme as they follow Claude's. After changing a chart, check both sides: the
gallery at `/dev/charts`, and `make mcp-app && make mcp-smoke` with the dev host.
