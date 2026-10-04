# M7: guardrails and shipping

M7 makes the project ready to run in public. The chat gets limits that keep a free demo within
budget, saved example answers for when it is limited, and a check of how it uses the tools. The
API gets a serve-only install, a bundle of the files it reads and a Modal app, and the site gets
its Vercel setup and security headers. The technical report and the model card bring the
project's findings together, with every number traced to its source by a test. No npm or Python
package was added: the dependency split moved existing packages into a group.

The plan's done-when is a live site, the remote MCP server drawing its charts inside Claude, the
report published and the repository public. None of that can happen without accounts, keys and
your decisions, so M7 is in two parts. Tonight's part holds: everything was built and checked
locally, against the scripted chat, in a production-like profile that runs the deployed
configuration minus Modal, Upstash and the real model. The morning's part is yours, about 75
minutes and under $1 of API spend, and is listed at the end of this report.

## What M7 builds

- **Guardrails on the chat** (`engine/src/race_engineer/api/limits/`): per-visitor question
  limits in a rolling hour and a UTC day, a daily spending cap from the chat's own token
  accounting (reserved before answering, settled after), a per-question cost ceiling checked
  before each call to the model, a global daily backstop, a kill switch and request-size limits.
  The counters live in Upstash Redis when it is configured and in memory otherwise, and an
  in-process pre-check answers refused and abusive traffic without touching the store. A
  visitor is a salted hash of the IP address that changes every UTC day. The limits fail
  closed: without a readable store the chat turns off and the tools keep working. The tools,
  the catalog and `/mcp` get per-minute request rates, and heavy tool calls a bounded queue.
- **Saved example answers** for the seven suggested questions: recorded by running the chat
  (`make saved-record`), served by the API at `/api/saved` and copied into the site at build
  time, and shown as "Saved answer from" a date whenever the chat is rate-limited, capped,
  paused, off or out of reach for a conversation that already loaded them.
- **Chatbot accuracy checks** (`engine/src/race_engineer/chat_eval/`, `make chat-eval`): fifteen
  questions with the tools they should call, checks on the arguments as the tool resolved them,
  behaviour checks and a grounding check that reads every number in an answer. The results stay
  in the repository and are not quoted on the site.
- **The API, ready to deploy:** a serve set without torch, MLflow, FastF1, matplotlib or ONNX
  Runtime (they moved to a `train` group that `uv sync` still installs by default); the
  served-data bundle (`make serve-data`, hard links, checked against the source data's health);
  `engine/modal_api.py`, which refuses to deploy from a shell that would leak the key or without
  the chart bundles; a Dockerfile for the Cloud Run fallback; stateless MCP; health and
  readiness routes; and CORS for the Vercel origin only.
- **The site, ready to deploy:** `npm run build:vercel` (an environment check, the tolerant
  snapshot and saved-answer prebuild, then `next build`), a Content Security Policy and security
  headers on production builds, an Open Graph image, `robots.txt` and `sitemap.xml`.
- **The remote MCP connector:** the same eight tools and their charts inside Claude as a custom
  connector, with nothing to install and no sign-in.
- **CI:** a size-budget check, a serve-only install check, a repository hygiene check (no data,
  weights, videos, saved answers or secrets tracked), an end-to-end run on synthetic data that
  exercises the rate limit and a $0 cap, and a manual-only API deploy workflow with no secret in
  the repository.
- **The technical report and the model card**, at the top of the site's "Full reports", with
  the human-checked precision kept in the repository copies only.
- **The final README** with three position-free screenshots, `docs/DEPLOY.md`, and a demo video
  pipeline (headless Chrome frames, captions, Swift and AVFoundation, no ffmpeg).

## Settings

The API's guardrail settings and their defaults (`engine/src/race_engineer/api/settings.py`).
All are environment variables and all are optional; a bad value stops startup with a message
naming the variable.

| Variable | Default | What it sets |
|---|---|---|
| `RACE_ENGINEER_LIMITS` | `auto` | `on`, `off`, or `auto`: on when the chat uses a real model and the API is bound to a host other than loopback |
| `RACE_ENGINEER_LIMITS_STORE` | `auto` | `auto`, `memory` or `upstash`: `auto` uses Upstash when both of its variables are set, else memory |
| `UPSTASH_REDIS_REST_URL`, `UPSTASH_REDIS_REST_TOKEN` | none | the Upstash Redis database that holds the counters; never logged |
| `RACE_ENGINEER_RATE_HOUR` / `RACE_ENGINEER_RATE_DAY` | 10 / 25 | questions per visitor in a rolling hour, and in a UTC day |
| `RACE_ENGINEER_DAILY_CAP_USD` | 3.00 | the daily spending cap, $3; 0 closes the chat |
| `RACE_ENGINEER_RESERVE_USD` | 0.10 | reserved for each question in flight, $0.10 |
| `RACE_ENGINEER_DAILY_QUESTIONS` | 1000 | the global backstop, 1,000 questions a day from all visitors |
| `RACE_ENGINEER_CHAT_MAX_COST_USD` | 0.30 | the per-question ceiling, $0.30 |
| `RACE_ENGINEER_LIMITS_COUNT_FAKE` | 0 | counts the scripted chat's spend (tests and the local cap check only) |
| `RACE_ENGINEER_PAUSED` | 0 | the kill switch without a store |
| `RACE_ENGINEER_TRUSTED_PROXY_HOPS` | 0 | how many proxies' `X-Forwarded-For` entries to trust, counted from the right |
| `RACE_ENGINEER_TOOLS_RPM` / `_TOOLS_GLOBAL_RPM` / `_MCP_RPM` | 120 / 600 / 240 | requests a minute: tools and catalog per visitor and in all, and `/mcp` in all |
| `RACE_ENGINEER_CHAT_RPM` / `_CHAT_GLOBAL_RPM` / `_STATUS_RPM` | 20 / 120 / 60 | requests a minute: `POST /api/chat` per visitor and in all, and `GET /api/chat/status` per visitor |
| `RACE_ENGINEER_CORS_ORIGINS` | the two localhost origins | the browser origins allowed; `none` allows none |
| `RACE_ENGINEER_CORS_ORIGIN_REGEX` | none | an origin pattern, for preview deployments; off by default |
| `RACE_ENGINEER_HEAVY_QUEUE` | 8 | heavy tool calls allowed to wait; the next is refused as busy |
| `RACE_ENGINEER_CHAT_MAX_BODY` / `_MCP_MAX_BODY` | 655360 / 524288 | the largest request body in bytes: 640 KiB on `POST /api/chat`, 512 KiB on `/mcp` |
| `RACE_ENGINEER_MCP_STATELESS` | 0 | stateless MCP with JSON responses (1 in production) |
| `RACE_ENGINEER_GZIP` | 1 | gzip for JSON responses of 1 KB or more |
| `RACE_ENGINEER_SSE_PAD` | 0 | bytes of padding at the start of each answer stream |
| `RACE_ENGINEER_SAVED` | `$RACE_ENGINEER_DATA/saved` | the saved example answers |
| `RACE_ENGINEER_SAVED_ALLOW_FAKE` | 0 | serves saved answers recorded with the scripted chat |
| `PORT` | none | the API's port when set |

In plain words, a deployed chat allows:

- 10 questions an hour and 25 a day per visitor. A visitor is a salted hash of the IP address
  that changes every UTC day; no IP address, cookie or question is stored.
- $3 a day of model spending in all, from the chat's own token accounting. Each question in
  flight reserves $0.10 until its cost is known, and a call still in flight with no usage
  reported when the question ends is charged $0.02.
- $0.30 for one question: checked before each new call to the model.
- 1,000 questions a day from all visitors, whatever they cost.
- Request bodies of up to 640 KiB on `POST /api/chat` and 512 KiB on `/mcp`.
- 120 requests a minute per visitor and 600 in all to the tools and the catalog; 20 a minute
  per visitor and 120 in all to `POST /api/chat`; 60 a minute per visitor to the chat status;
  240 a minute in all to `/mcp`.

The limits fail closed: when the counters can't be read, the chat turns off and the site shows
saved answers, while the tools, the catalog and `/mcp` keep working.

## How it was checked

Every check ran on 2026-10-04 between 07:15 and 07:50 EDT, on the M7 branch with every step's
work in place, against the scripted chat (`RACE_ENGINEER_CHAT=fake`). Nothing touched an
account, a key or a paid service.

### The production-like profile

The deployed configuration, minus Modal, Upstash and the real model:

1. `make saved-record API=http://127.0.0.1:8000 ALLOW_FAKE=1`: the seven saved answers,
   recorded from the scripted chat into `data/saved/`.
2. `make serve-data ALLOW_FAKE=1`: `data/serve`, 1,856 files and 6.83 GB as hard links, with
   health the same as the source data's (263 sessions, 18,762 mistakes, the same scoring run).
3. `make api-prodlike`: uvicorn on `127.0.0.1:8001` with `RACE_ENGINEER_HOST=0.0.0.0` (so the
   app behaves as on a public host), the served-data bundle, stateless MCP, the limits on with
   the memory store and the scripted chat's notional cost counted, CORS for port 3001 only, and
   `--no-proxy-headers`. Each limits row restarts it with its own setting from
   `.claude/launch.json`.
4. The static saved copies pulled into `web/public/saved/` from the API, as the Vercel build
   does (removed again afterwards; the folder is gitignored).
5. `make web-prodlike`: `next build` with `NEXT_PUBLIC_API_URL=http://127.0.0.1:8001`, then
   `next start` on port 3001, with the Content Security Policy on.

### Checks

| Check | Result | Time |
|---|---|---|
| `make check` | ruff (165 files) and its format check clean, pyright 0 errors, pytest 1,006 passed; eslint clean, `tsc` clean, `npm test` 684 of 684, the script tests 15 of 15 | 109 s |
| The web CI steps | lint, typecheck and tests as above; `next build` (web-prodlike's build: every route static or prerendered); `npm run budgets` (below); `npm run build:mcp-app` (`make mcp-app`: six bundles). `npm ci` wasn't run, because it would replace the shared `node_modules`; `package.json` and the lock agree | 3 s for the bundles |
| `make mcp-smoke` (stdio) | 8 tools, 6 chart resources, 11 calls, 0 problems | 5 s |
| `make mcp-smoke URL=http://127.0.0.1:8001/mcp` (stateless) | 8 tools, 11 calls, 0 problems, warm and again from a cold process | 3 s |
| `make chat-smoke` (in process) | 0 problems; notional cost $0.0591 | 4 s |
| `make chat-smoke URL=http://127.0.0.1:8000` | 0 problems (limits off on `api-fake`) | 1 s |
| `make site-smoke` (the dev pair, 3000 and 8000) | 67 checks: 65 ok, 0 failed, 2 skipped as expected (no static saved copies; `next dev` sends no security headers) | 7 s |
| `make site-smoke SITE=http://localhost:3001 API=http://127.0.0.1:8001 ORIGIN=http://localhost:3001` | 66 of 66 ok, nothing skipped: 23 pages, the 3 missing pages 404, the hero file, the 7 static saved answers with their guards, the CSP naming the API in `connect-src`, the manifest and icons, links and anchors, the API's routes, 7 featured corners, 17 scripted questions with their charts, `invalid_history`, the 9th question's `turn_limit`, CORS for port 3001 only | 3 s |
| `make limits-burst URL=http://127.0.0.1:8001` | answers 1 to 10, then 429 `rate_limited`, `limit=hour`, `retry_after_s` 3600; 0 problems | under 1 s |
| Spoofed `X-Forwarded-For` and `X-Real-IP` on `/api/chat/status` | the visitor tag doesn't change | |
| A 700 KiB chat body; a preflight from `https://example.com` | 413; refused (400) | |
| The CI `e2e` job, step by step on private ports | the synthetic tree with health ok; `ci-smoke --phase rate` 10 of 10 (the third question 429 with `Retry-After` exposed); `--phase cap` 5 of 5 (`daily_cap` before any question, the synthetic saved answer loads and passes its guards) | 40 s |
| `make serve-venv-check` | a fresh serve-only install: no training package, the app starts on empty data, health 200 degraded, ready 503, stateless `/mcp` lists 8 tools; 0 failed of 10 | 18 s |
| `engine/modal_api.py` in a scratch venv with only the serve set and `modal` | imports offline, loads no training package, and its deploy check finds 0 problems with the bundles built | 13 s |
| `make budgets` | every page within its limit (table below) | |
| `make demo-video SITE=http://localhost:3001 API=http://127.0.0.1:8001` | a 75.0 s draft, 1280 × 720, 30 fps, H.264, 17.1 MB, in `data/demo/` (gitignored) | 90 s |
| `.github/scripts/check-tracked.sh` on the tree with every change staged in a scratch index | passes | |

`make chat-eval` (the in-process scripted run) was run by step A's check and again by the final
check: the 15 questions in 1.4 s, written only under `data/evals/` (a scripted run tests the
harness and is not a finding). Its real run is step 2 of the morning.

### Browser-pane rows

Each row ran in the Browser pane against the production-like pair, with the console and the
network read after every page.

| # | Setup | Result |
|---|---|---|
| 1 | `RATE_HOUR=3` | Phone size, Dark: three questions answered; the fourth gives "You've asked 3 questions in the last hour from this connection", the minutes left ("Ask again in 60 min"), Send disabled with the draft kept, and saved-answer chips. The fourth question matched a saved answer, which showed at once ("SAVED ANSWER · 4 OCT 2026", recorded earlier with the same tools). Another saved answer opened with its chart, "What Claude saw" and a working Show telemetry; the status still read 0 left this hour and 22 today, so saved answers count nothing. A reload restored all of it. Desktop, Light: the same notice and chips. The only console line is the expected 429 |
| 2 | `CAP=0.01` | `/chat` says "The chat has used today's budget for this free demo. It resets at midnight UTC." before any question, with the seven saved answers. The landing page's Ask box with a suggested question's text leads to `/chat`, where nothing is sent, the composer keeps the question and the matching saved answer shows |
| 3 | `PAUSED=1`, then `CHAT=off` | "The chat is paused for now." with saved answers (phone, Dark); then "The chat is switched off on this server." with the explorer links and saved answers (phone, Light) |
| 4 | `web-prodlike` | `/`, `/chat`, `/mistakes`, `/styles`, `/report` and three report pages: no console message at all, so no CSP violation (a deliberate fetch to another origin was blocked and logged, which shows the policy is enforced and the console catches it). The theme script runs under the policy: a stored Light gives `data-theme="light"` before paint, System follows the device, nothing stored gives Dark. Charts drawn, calls to port 8001 allowed. `/opengraph-image` is 1200 × 630 with the lockup; `/twitter-image` answers; `robots.txt` allows `/`, disallows `/dev/` and names the sitemap; the sitemap lists the 5 pages and the 18 reports. Locally both use `http://localhost:3000`, the fallback site URL; on Vercel they use the production URL |
| 5 | `web-prodlike` | `/report`: "Technical report" and "Model card" open "Full reports", and the connector line names the API's `/mcp`. Both pages render with the omission note where "Human-checked precision" is in the repository copy, and none of the review's figures is on them. At 360 px wide, `/report`, both pages and this report have no sideways scroll |
| 6 | the API stopped | M6's behaviour is unchanged: `/chat` shows "Connecting to the analysis server…", then "Can't reach the analysis server" with Try again, and `/mistakes` its own notice, while the landing page and the findings work. A saved answer loaded before the API stopped still shows after a reload, from the static copy |

### Timings and memory in the production-like profile

Measured on this Mac (macOS resident memory, which counts the data files the process has mapped;
Linux on Modal will differ somewhat).

| What | Measured |
|---|---|
| `api-prodlike` from `make` to ready | 13.4 s, with the warm-up (health's `warm_s`: catalog 0.24 s, `find_session` 0.03 s, `compare_driving_styles` 0.14 s) |
| API memory when ready | 535 MB |
| API memory at its peak | 1,161 MB over the site smoke (17 questions, 7 explained corners), the MCP smoke, the screenshots and the demo video; 1,070 MB over a cold MCP smoke and the burst. Both under the 2 GiB the Modal app requests |
| Tools over stateless `/mcp`, first calls after a restart | `explain_corner` 0.34 s; every other call 0.06 s or less |
| Explained corners through REST (the landing page's featured corners) | 137 to 500 ms each |
| Catalog routes | 8 to 18 ms |
| A scripted chat answer | 6 to 124 ms to `done`; 0.01 s each in the burst |
| `next build` | about 10 s (compiled in 1.2 s, TypeScript 2.9 s, 43 static pages in 0.7 s); `next start` ready in 69 ms |
| Pages from `next start` | 3 to 38 ms each |

### Size budgets

Initial JavaScript per page from the production build (gzip, KiB of 1,024 bytes), against the M6
baseline plus 5% (decision 11 of the plan, recomputed from `m6_website.md` as merged).

| Page | KiB | M6 | Limit |
|---|---|---|---|
| `/` | 186.8 | 185.8 | 196 |
| `/chat` | 200.4 | 194.3 | 205 |
| `/mistakes` | 191.2 | 190.7 | 201 |
| `/styles` | 193.3 | 191.5 | 202 |
| `/report` | 151.9 | 151.1 | 159 |
| `/report/technical-report` (any report page) | 139.9 | 139.1 | 147 |

`/chat` grew 6.1 KiB with the saved answers and the limits UI, although the saved-answer view
loads on demand; it has 4.6 KiB left. The other pages grew 0.5 to 1.8 KiB, mostly from one extra
Next.js chunk once the metadata routes exist.

## The pre-public audit

Read-only, on every commit up to the M7 branch's wave 1 commit (`80608ba`).

- **The human review is in the repository.** `report/m4_review.md` (the reviewer's verdict and
  notes per finding), `report/m4_review_labels.json`, `report/m4_review_claude.json` and
  `report/m4_review_preread_v1.csv` are all tracked, and have been in the history since commit
  `b350b64`. The versions of `report/m4_summary.md` before `ae34774` carry per-finding review
  details. `report/m4_review_queue.csv` (the review queue: the flagged corners and whether each
  was inspected, no verdicts) has been tracked since the same commit. Deleting a file now would
  leave it in the history and in the pull-request pages, which GitHub keeps. This is question D1
  of the morning.
- **Renderings of real data:** `report/figures/*.png` (seven figures, among them a track map
  coloured by speed), the four image outputs in `engine/notebooks/01_explore_telemetry.ipynb`,
  and M7's three screenshots in `docs/screenshots/` (a driving-styles chart from a chat answer,
  the report's detector comparison and the style map: no track map, replay or running order).
  This is question D2.
- **No data, weights or videos were ever committed.** Of the 534 paths ever added, none is under
  `data/`, `models/`, `web/public/snapshots/` or `web/public/saved/`, and none is a parquet,
  npz, npy, checkpoint, ONNX, pickle or video file. The JSON files are package and config files,
  the synthetic chart fixtures (`web/src/mcp-app/sample-*.json`), the synthetic saved-answer
  fixture and the two review files above. The demo video and the saved answers stay in
  `data/`, which is gitignored and checked by the hygiene job.
- **The secret scan is clean.** Across every commit, the only key-like string is the test
  placeholder `sk-ant-test-not-a-real-key` in `engine/tests/test_api_tools.py`, and the only
  value given to a secret setting is the development chat secret `dev-only-not-secret` in the
  Makefile. No `.env` file other than `web/.env.example` was ever tracked. The working tree,
  M7's new files included, has no key, token or personal email address.
- **Every author is a `noreply` address:** all commits are by `yunichun16`, with
  `yunichun16@users.noreply.github.com` or GitHub's own merge address.
- **A clean snapshot is staged** for option B of D1, outside the repository and never pushed:
  the tree that `main` holds once the M7 pull request is merged, without the four review files,
  with links to them turned into plain text, in one commit, with the web tests passing there.

## Decisions taken overnight

The plan's defaults stand unless you say otherwise; each is reversible.

- **The human-checked precision stays in the repository copies** of the technical report and the
  model card, in a section the site leaves out, as `m4_summary.md` does. The README quotes no
  accuracy figure.
- **The chat accuracy report stays off the site** (`m7_chat_accuracy.md` is excluded from the
  manifest) until you decide (D4).
- **CI's size budgets are "no regression past M6 plus 5%"**, since four pages were already over
  M6's original targets.
- **The Content Security Policy allows inline scripts** (`script-src 'self' 'unsafe-inline'`):
  a static Next.js page carries inline payload scripts that change with every build, and nonces
  would make every page dynamic. Everything else is strict.
- **The model weights are to be published** as release assets of the public repository (D3), as
  the project's licensing decision says; the model card says "coming with the public release"
  until then.
- **The status route keeps its per-visitor rate only.** A global rate would need a new setting,
  which would change an interface frozen for the night (see Known risks).
- **The production-like rows ran with the static saved copies in place**, as the Vercel build
  makes them. Without them a limited `/chat` logs one 404 for `/saved/index.json` before it
  reads the API's list; that was left as designed.
- **The chat screenshot shows the chart card only**, because tonight's answers are scripted and
  say so in their text.
- **The clean snapshot is built from the working tree as it will be merged**, not from today's
  `main`, which doesn't have M7 yet. Its script can rebuild it from `main` once the merge is in.

## Known risks

- **The chat status route has a per-visitor rate only, not a global one.** Someone rotating
  through IPv6 /64 prefixes could spend Upstash's free monthly commands through status reads
  (each read is cached for 10 s per visitor). The chat would then fail closed to saved answers
  for the rest of the month; no money would be spent. The fix is a global status rate, a new
  setting for a follow-up.
- **The client address on Modal is inferred, not documented.** If Modal doesn't put the real
  client in the ASGI scope, the chat refuses rather than sharing one quota; `make deploy-check`
  checks the visitor tag and that spoofed headers don't change it.
- **The Upstash client was tested against a fake** built from Upstash's documentation. `make
  limits-check` runs the store's contract on the real database before the chat depends on it.
- **A remembered `daily_cap` closes the chat for everyone for up to 60 s**, even when in-flight
  reservations rather than spending caused it.
- **Memory on Linux** with two heavy calls at once is estimated, not measured; the Modal app
  requests 2 GiB and is restarted only past 4 GiB. Modal's memory graph during `make
  deploy-check` shows it.
- **Traffic past Modal's $30 monthly credit is billed** if a card is on file (step 1 of the
  morning).
- **The grounding check's "derived" numbers are a weak guard** on long tool summaries: in the real
  run, read that list as possible fabrications, especially for corner explanations.
- **`/chat` has 4.6 KiB of budget left**, so the next feature there needs to load on demand.
- **Saved answers go stale** as races are added; `recorded_at` is shown, and re-recording is
  `make saved-record`, `serve-data`, `serve-upload`, `deploy-api` and a Redeploy on Vercel.
- **Whether Safari holds back the first bytes of a stream** is unknown; the phone check in step 9
  and `RACE_ENGINEER_SSE_PAD` cover it.
- **Whether Claude's connector accepts stateless JSON MCP** is settled by step 10; the fallback is
  `RACE_ENGINEER_MCP_STATELESS=0`.
- **The clean snapshot lives in a temporary folder** that a restart of the Mac empties; its
  script rebuilds it in seconds.

## What needs you in the morning

About 75 minutes in the morning, under $1 of API spend in all. Run everything from **your own
terminal**, not the one Claude Code runs in (that one sets `ANTHROPIC_BASE_URL`, and a server
started there would send your key through it). Never paste a key or token into the chat with
Claude.

```bash
cd /Users/default/Developer/race-engineer
git checkout main && git pull                       # the overnight summary says whether the M7 PR merged; if not: git checkout m7-ship
unset ANTHROPIC_BASE_URL
make mcp-app                                        # the chart bundles (gitignored), needed by make api's /mcp and by deploy-api
```

**1. A dedicated Anthropic key with a spend limit, and Modal's billing (5 min).** In the
Anthropic Console, create a key for this site (ideally in its own workspace) and set a monthly
spend limit there, for example $20: a backstop independent of the site's own $3/day cap. Then,
in this terminal only:

```bash
read -rs ANTHROPIC_API_KEY && export ANTHROPIC_API_KEY    # paste, then Enter; nothing is echoed
```

In Modal's dashboard, open the workspace's usage and billing page: if a payment method is on
file (M3's GPU training may have added one), usage past the $30 monthly credit is charged to it.
Set a workspace budget there if Modal offers one; otherwise note it. Kept warm all month the API
uses about $29 (the costs are in [DEPLOY.md](../docs/DEPLOY.md#costs)), and `cd engine && uv run
--group cloud modal app stop race-engineer-api` stops it at once.

**2. The real-key runs (15 min, about $0.20-0.55).**

```bash
make api                                            # this tab (it has the key); /api/health should say "mode": "anthropic"
make chat-eval URL=http://127.0.0.1:8000            # a second tab (needs no key): 15 questions, ~$0.15-0.40 → report/m7_chat_accuracy.md
make saved-record API=http://127.0.0.1:8000         # 7 saved answers, ~$0.05-0.15 → data/saved/
```

Stop `make api` afterwards (Ctrl-C). `make chat-eval` exits non-zero if any question failed a
check; the report is written either way. Leave `report/m7_chat_accuracy.md` uncommitted; Claude
commits it in step 13.

**3. Build and upload the served data (5 min, then about 17 min of upload at 50 Mbps).**

```bash
make serve-data                                     # data/serve: hard links + the 7 real saved answers, checked
make serve-upload                                   # creates the race-engineer-serve volume and uploads (in its own tab)
```

Steps 4 and 5 can be done while it uploads; step 6 needs the upload finished (it prints
"uploaded N files").

**4. Upstash (5 min).** Sign up at upstash.com (no card), create a Redis database on the free
plan with primary region AWS us-east-1 (N. Virginia; the region only changes latency, two round
trips per question) and no read regions, and keep its page open for the REST URL and the
read-write REST token (not the read-only one). Optional check from this terminal: `read -rs
UPSTASH_REDIS_REST_URL && read -rs UPSTASH_REDIS_REST_TOKEN && export UPSTASH_REDIS_REST_URL
UPSTASH_REDIS_REST_TOKEN`, then `make limits-check` (writes test keys under `re:test:…` and
deletes them).

**5. The Modal Secret (3 min).** In the Modal dashboard: Secrets → Create new secret → Custom,
named `race-engineer-api`, with four keys:

- `ANTHROPIC_API_KEY`: the key from step 1;
- `RACE_ENGINEER_CHAT_SECRET`: the output of `openssl rand -hex 32`;
- `UPSTASH_REDIS_REST_URL` and `UPSTASH_REDIS_REST_TOKEN`: from step 4.

**6. Deploy the API (3 min).**

```bash
make deploy-api                                     # prints https://<workspace>--race-engineer-api.modal.run; no browser origin allowed yet
export API=https://<workspace>--race-engineer-api.modal.run
curl -s $API/api/health | python3 -m json.tool      # status ok, chat.mode anthropic, chat.available true, limits.enabled true, limits.store upstash, limits.problems []
```

**7. Vercel (10 min).** Sign up at vercel.com with GitHub (the Hobby plan, which is for
non-commercial use like this) and let its GitHub app access only `yunichun16/race-engineer`. Add
New → Project → import the repository → Root Directory: `web` → leave "Include files outside the
Root Directory" on → Environment Variables: `NEXT_PUBLIC_API_URL` = the value of `$API`
(Production and Preview) → Deploy. The build fails on purpose if that variable is missing. Note
the production URL (`https://<your-project>.vercel.app`). `NEXT_PUBLIC_SITE_URL` is needed only
if you later add your own domain.

**8. Let the site call the API (1 min).**

```bash
export SITE=https://<your-project>.vercel.app
make deploy-api SITE_ORIGIN=$SITE
```

**9. Check it (5 min).**

```bash
make deploy-check SITE=$SITE API=$API               # pages, hero.json, saved answers, CORS, CSP, /mcp, limits, client address,
                                                    # timings; asks one real question (~$0.01) to check streaming through Modal
```

(`CHAT=skip` asks nothing.) It prints your `visitor` tag. Then open the site on the Mac and on
your phone (Safari, on cellular, not Wi-Fi): ask one question on `/chat` and watch the answer
stream in; open `$API/api/chat/status` on the phone and check its `visitor` differs from the
Mac's. While you're on the phone, the reduced-motion check from M6 (Settings → Accessibility →
Motion → Reduce Motion: the hero shouldn't rotate on its own). If the check reports
`explain_corner` over 2 s, or Modal's memory graph sits above 2 GiB, tell Claude (the
troubleshooting table in [DEPLOY.md](../docs/DEPLOY.md#troubleshooting) says what changes).

**10. The Claude connector (5 min).** In Claude: Settings → Connectors → Add custom connector →
name "Race Engineer", URL `$API/mcp`, no sign-in → enable it in a new chat from the tools menu →
ask the seven M5 questions (the most recent race; the 2023 Monaco GP; Gasly's mistakes in the
2025 Abu Dhabi race; the corner where he lost the most; Norris against Piastri in 2025 Abu Dhabi
qualifying; Hamilton's style against Leclerc's; the biggest mistakes in the last race). Each
chart should render.

**11. Decisions before going public (10 min).**

- **D1, the human review in the history.** The per-finding verdicts (`report/m4_review.md`,
  `m4_review_labels.json`, `m4_review_claude.json`, `m4_review_preread_v1.csv`, and older
  versions of `m4_summary.md`) have been in the history since commit `b350b64`, and GitHub keeps
  them in the pull-request pages even if the history is rewritten. Choose:
  - **(A)** make this repository public as it is (the verdicts become public); or
  - **(B)** publish a new public repository from the clean snapshot Claude staged overnight
    (outside the repository: the current `main` without those files, one commit), and keep this
    one private. Claude gives the exact `gh repo create … --public --source … --push` command
    when you choose B.
- **D2, renderings of real data:** `report/figures/*.png` (7, including `track_map_speed.png`),
  the four image outputs in the exploration notebook, the three committed screenshots (a styles
  chart in the chat, the detector figure, the style map: no track maps, replays or running
  order), the demo video, and any screenshot of the hero, the race-summary chart or the explorer
  maps. Default: keep the figures, the notebook and the three screenshots (renderings like the
  site's own charts); keep the video and any map, replay or running-order screenshot out of the
  repository and attach the video to the README through GitHub's editor.
- **D3, the model weights:** the master plan's licensing decision is to publish the model, so the
  default is to attach `mae.pt` (2.33 MB) and `mae_scorer.onnx` (2.61 MB) to a GitHub Release of
  the public repository, which Claude creates once you confirm. Say no to keep them private (the
  model card then says so).
- **D4, the chat accuracy report on the site:** publish `/report/m7-chat-accuracy`? Default:
  repository only.
- **D5, build-time data:** snapshots, `hero.json` and saved answers are generated at each deploy
  from the API (default). Committing them would put derived positions in the repository; only if
  you explicitly want that.
- **D6, a warm container:** keep one container always on (`min_containers=1`, about $29 a month
  of the $30 Modal credit at the 0.5-core request, more than the credit at 1 core) for no cold
  starts? Default: scale to zero with a 5-minute idle window.
- **D7, previews:** should Vercel preview deployments call the production API (`make deploy-api
  SITE_ORIGIN=$SITE PREVIEW_REGEX='^https://race-engineer-[a-z0-9-]+-<your-team-slug>\.vercel\.app$'`)?
  Default: no.
- **Defaults taken overnight** (reversible, say if you disagree): the technical report and the
  model card keep the human-checked precision in the repository copy, in a section the site
  leaves out, as `m4_summary.md` does; the README quotes no accuracy figure; CI's size budgets
  are "no regression past M6 plus 5%"; the CSP allows inline scripts.

Then, for (A): `gh repo edit yunichun16/race-engineer --visibility public
--accept-visibility-change-consequences`. For (B): the command Claude gives you. Afterwards set
`NEXT_PUBLIC_REPO_URL` in Vercel to the public repository's URL and redeploy.

**12. The demo video (5 min, about $0.02).**

```bash
make demo-video SITE=$SITE API=$API                 # → data/demo/race-engineer-demo.mp4 (about 75 s, captions, no audio)
```

Watch it; for a narrated version or the Claude-connector scene, `docs/demo.md` has the
storyboard and a QuickTime recording script.

**13. Tell Claude** the site URL, the API URL, what `deploy-check` printed (or that it passed),
and your answers to D1-D7. It then updates the README's links and the site's repository link,
commits `report/m7_chat_accuracy.md` (and publishes it if D4 says so), applies D2-D3 (creating
the weights' release only with your confirmation), and opens and merges one small follow-up PR.
Never paste a key, token or the chat secret.

**Nothing here needs a package approval:** M7 adds no npm or Python packages.
