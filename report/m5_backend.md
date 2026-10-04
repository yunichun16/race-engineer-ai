# M5: backend and MCP tools

M5 turns the M4 results into something people can ask questions of. One set of seven analysis
tools is served three ways: by an MCP server, which draws five of them as interactive charts
inside Claude; by a FastAPI app with a REST route per tool; and by a chat endpoint where Claude
Sonnet 5.5 picks the tools and answers from them. Everything here runs locally, and every check
that needs no API key or Claude desktop app passes. A chat run with a real API key passed on
2026-10-03, before find_session's redesign (section 2), and the reviewer checked every chart
inside the Claude desktop app the same day. What remains is optional: one more real-key run
with the redesigned find_session.

The plan's done-when has three parts. "Every tool works in the MCP Inspector" is covered by
`make mcp-smoke`, which passes over stdio and over HTTP; the Inspector itself needs your OK to
download. "Every tool works inside Claude with its chart" passed: the reviewer asked the
seven MCP questions in the Claude desktop app, and every chart worked. "The chat
answers the example questions locally" passes with the scripted stand-in for Claude, and passed
with Claude Sonnet 5.5 and a real key.

## 1. What M5 built

### One tool layer

Each tool is a `ToolSpec` in `engine/src/race_engineer/tools/`: a name, a description written
for the model, a pydantic `Params` model for its inputs, a `run` function and, for chart tools,
the chart bundle that draws the result. `tools/definitions.py` lists them in a fixed order
(`TOOLS`), because the chat sends the tool list at the start of every request, where it is part
of the cached prompt. The MCP server, the REST routes and the chat all build from `TOOLS`, so a
tool has the same description, input schema, validation and answer everywhere; a test checks
that the MCP server lists exactly the specs' schemas, `additionalProperties: false` included.

A tool answers with a `summary`, the text the model reads, and `data`, the chart payload. The
model never sees the data: it goes to MCP `structuredContent`, to the REST response and to the
browser over the chat's event stream. `run_tool` validates the arguments (one readable line per
mistake, e.g. "lap: must be at least 1", and "unknown parameter(s) ..." for an argument the tool
doesn't have), runs the tool and caches the result until the data changes, so `make data` or
`make m4` while a server runs takes effect on the next call. The MCP server checks the arguments
the same way before the MCP SDK sees them, and the REST routes word their 422s the same way, so
all three refuse the same inputs with the same words. Heavy tools (explain_corner, compare_laps)
run at most two at a time.

| Tool | Answers | Chart |
|---|---|---|
| `find_session` | which processed session the user means, from fields the model fills in (decision 8): `recent` counts back from the newest processed session ("last race": `recent=0, session="R"`; "the race before last": `recent=1, session="R"`), `round` picks a weekend ("round 5 2024"), `event` takes only the name the user gave, a location, country or session key ("Monza", "Silverstone", "2025_16_Q"), with `year` and `session`; then the rest of that weekend, what can be analysed for it and the newest processed season; for a misspelt or ambiguous name, candidates, best match first; for an event with no session in the season asked for, its other seasons', to tell the user about; for event text that isn't a name (a question, a driver), none, only how to fill the fields | — (structured data) |
| `list_sessions` | every processed session, by season and weekend | — |
| `get_race_summary` | a race or sprint from the timing data: order as timed, gaps, lapped and stopped cars, positions from the end of lap 1, SC, VSC and red flags, pit stops, stints, fastest lap; optionally one driver's race | `race-summary`: positions lap by lap |
| `find_mistakes` | the biggest driver mistakes in a session, with type, time lost and an explanation, traffic left out | `find-mistakes`: the list and a track map |
| `explain_corner` | one corner on one lap against the driver's usual: time lost by phase, braking and throttle points, traffic, the detectors' verdict | `explain-corner`: traces with the usual range, a map and a replay of the cars around |
| `compare_laps` | two drivers' laps: where one gained on the other | `compare-laps`: speed, gap, pedals |
| `compare_driving_styles` | how two drivers take corners over a season, by corner type and session, with intervals and embedding similarity | `compare-styles`: differences and a style map |

`show_sample_telemetry`, the M0 spike on a synthetic lap, stays on the MCP server only.

### The MCP server

`race-engineer-mcp` serves the seven tools plus `show_sample_telemetry`, with six chart bundles
(`telemetry`, `compare-laps`, `find-mistakes`, `explain-corner`, `compare-styles`,
`race-summary`), each a single HTML file built by Vite from `web/src/mcp-app/` (`make mcp-app`).
Chart tools carry `_meta.ui.resourceUri`; `find_session` and `list_sessions` carry none. The
server's instructions name every tool and tell the model to call `find_session` first when the
session is unclear. It runs over stdio for the Claude desktop app, over streamable HTTP on its
own (`make mcp-http`, port 8765), and inside the API at `/mcp`.

### The API

`race-engineer-api` (`make api`) serves `GET /api/health`, `GET /api/tools`, one
`GET /api/tools/<name>` route per tool with the arguments as query parameters, `POST /api/chat`
and `/mcp`, with the OpenAPI docs at `/docs`. Health is always 200 and says `degraded`, with
reasons and no absolute paths, when data is missing or stale; missing data never stops startup.
Errors share one JSON shape: 422 `invalid_request` (bad arguments) or `invalid_input` (the
tool's own message, e.g. an unknown driver), 503 `results_unavailable` (the M4 results are
missing or from another scoring run), 404 `unknown_tool`, 500 `internal_error` with a request id
and the trace only in the log. Tool responses carry an ETag. CORS allows the Next.js dev server
(localhost:3000). `/mcp` accepts only localhost Host headers while bound to localhost.

### The chat

`POST /api/chat` answers with Claude Sonnet 5.5 through the Anthropic SDK's tool runner, at
medium effort with adaptive thinking whose progress notes stream as status lines, and streams
server-sent events: `status`, `text`, `tool_call`, `tool_result` (with the chart data),
`refusal`, `error` and, always last, `done`. The server keeps no transcripts: `done` carries the
whole history, signed with an HMAC, and the browser sends it back with the next question. A
tampered history is refused (400), one from an older prompt or tool list is told to start over
(409), and a conversation stops at 8 questions (409). The tools and system prompt are cached,
and each question logs its tokens and cost, never its text. `RACE_ENGINEER_CHAT=fake`
(`make api-fake`) replaces Claude with a scripted stand-in that streams real Anthropic events
through the real SDK, so the whole pipeline runs without a key.

### The charts

Four new MCP App pages join compare-laps and telemetry: find-mistakes (the ranked list and a
track map whose markers grow with the time lost; an Explain button asks Claude about a row
where the host allows it), explain-corner (speed, time-lost, throttle and brake traces with the
usual range and "where the time went" shaded, plus a map and an animated replay with the ghost
lap), compare-styles (differences by corner type and session, and the season's style map) and
race-summary (positions lap by lap with the neutralisations and pit stops). Each keeps a pure
`render(root, data)` apart from the MCP wiring, so M6 can reuse it in React. All follow the
host's theme, work by keyboard and at 360 px.

### Fixtures and smoke scripts

- `make mcp-app-fixture` writes 17 sample results for the dev host (`make mcp-app-dev`): the
  main fixture of each page plus variants (find-mistakes `-driver`, `-empty`, `-error`;
  explain-corner `-traffic`, `-noreplay`, `-error`; compare-styles `-rivals`, `-sparse`,
  `-error`; race-summary `-small`, `-error`). Every one comes from the real tool on synthetic
  data, errors included; none is hand-made and none uses real F1 data.
- `make mcp-smoke [URL=...]` (`engine/scripts/mcp_smoke.py --strict`) does what an MCP
  Inspector session would on real data: lists the tools and their chart bindings, reads every
  chart bundle and fails on the "not built" placeholder, calls each tool with the example
  questions' arguments (chaining a `find_mistakes` row into `explain_corner`, and adding the
  largest real style payload, ALB and LEC in 2024) and checks each result's keys, summary
  length, payload size and time target (explain_corner under 2 s cold, the others under 1 s).
- `make chat-smoke [URL=...]` (`engine/scripts/chat_smoke.py --strict`) asks the chat the
  example questions in one conversation, then the ninth question (must be refused), a question
  no tool answers and, with the scripted chat, a refusal. A question fails when its expected
  tool isn't called or every call of it is an error. Question 3 asks about Gasly in the 2025
  Abu Dhabi race instead of appendix B's Leclerc in Monza 2025 qualifying: Leclerc has no
  flagged mistake there, so question 4's follow-up would have no corner to explain.

## 2. Checks run for this report

| Check | Result |
|---|---|
| `make check` (ruff, eslint, pyright, `tsc`, pytest) | passes: 659 passed, 1 skipped, in 71 s; after decisions 7 to 9, ruff, pyright and pytest again: 812 passed in 78 s with the real data |
| Web CI: `npm run lint`, `npx tsc --noEmit --incremental false`, `npm run build`, `npm run build:mcp-app` | all exit 0 |
| `make mcp-app` | 6 bundles built (235-264 KB each) |
| `make mcp-app-fixture` | 17 fixtures; a second run writes the same bytes; compare-laps and telemetry unchanged (the others changed only as decisions 1, 5 and 10 say) |
| Dev host, every fixture | all 17 render, no console errors (re-checked after the review fixes on the six main fixtures and the -traffic, -noreplay, -driver and -rivals variants; the traffic map now names BBB) |
| `make mcp-smoke` (stdio, time targets enforced) | 0 problems (re-run after decisions 8 and 9) |
| `make mcp-smoke URL=.../mcp` (the API's `/mcp`, scripted chat mode) | 0 problems |
| `make chat-smoke` (scripted, in-process) | 0 problems; every expected tool answers, question 4's explain_corner included (re-run after decisions 8 and 9: questions 1 and 7 call `find_session(recent=0, session="R")`) |
| `make chat-smoke URL=...` (scripted, against `api-fake`) | 0 problems, the same |
| `make chat-smoke URL=...` against `make api` with a real key (2026-10-03, at commit b585bdd, before decisions 8 and 9) | 0 problems; total cost $0.12 |

The smoke runs used the real dataset: 263 sessions (2022 to 2026 round 15, Baku), the current
scoring run with 18,762 mistakes, the style tables and the circuit rotations.

**The real-key run, in detail:**
- **Tools:** all 8 example questions called the expected tool with fitting arguments, and every
  chart tool returned its chart. Question 1 called `find_session(query="last race", session="R")`,
  with the interface before decision 8. Question 7 reused question 1's session and called
  `find_mistakes` directly.
- **History:** the API accepted the browser-held, signed history with its thinking blocks on
  every follow-up (questions 2-8). This was the open question in section 6.
- **Limits:** the ninth question got 409 `turn_limit`. The 2027 question was declined without a
  tool.
- **Caching:** cache reads were above 0 on every question and grew from 6,088 to 30,822 tokens.
- **Cost and time:** $0.0035-0.0196 per question, $0.12 in all; 3-8 s per answer.

## 3. Timings

From `make mcp-smoke` over stdio, a fresh server process, each call the first of its kind:

| Call | Seconds | Summary (chars) | Payload |
|---|---|---|---|
| `find_session(recent=0, session="R")`, the last race | 0.17-0.29 | 389 | 0.7 KB |
| `list_sessions(year=2025)` | 0.00 | 2,691 | — |
| `get_race_summary` Monaco 2023 | 0.08 | 1,570 | 10.8 KB |
| `find_mistakes` Monza 2025 Q, LEC | 0.05 | 715 | 5.6 KB |
| `find_mistakes` Baku 2026 R, whole field | 0.05-0.06 | 3,400 | 10.7 KB |
| `explain_corner` Baku 2026 R, NOR lap 16, T5 (cold) | 0.32-0.38 | 1,537 | 26.6 KB |
| `compare_laps` Abu Dhabi 2025, PIA and NOR | 0.02 | 915 | 48.0 KB |
| `compare_driving_styles` HAM and LEC (2026) | 0.05 | 1,534 | 24.2 KB |
| `compare_driving_styles` ALB and LEC 2024 (the largest style payload) | 0.04 | 1,944 | 29.4 KB |
| `get_race_summary` Baku 2026 R | 0.04 | 1,639 | 10.4 KB |

Every call is far inside the targets (explain_corner under 2 s cold, the others under 1 s). The
first call in a process pays for the session catalog (about 0.2 s), which the API loads at
startup. More, from the step checks and REST calls on the running API:

| Item | Value |
|---|---|
| API start to the first `/api/health` 200 | 3.5 s (`uv run` included); the startup itself 0.3 s: catalog 0.22 s, style tables 0.08 s |
| `explain_corner` Monaco 2023 R, ALB lap 51, T11 (REST, cold session) | 0.46 s; 0.70 s as the first call of a fresh process (step C2) |
| `find_mistakes` Monaco 2023 R, whole field | 0.06 s |
| `get_race_summary`, all 133 races and sprints with every driver as the focus (2,808 calls, step B) | slowest 0.083 s |
| `compare_driving_styles` HAM and LEC 2025, warm (step D) | median 0.034 s |
| A cached result | about 1 ms |

## 4. Payload sizes and memory

**Payloads.** The chart data of every tool stays within 30 KiB (30,720 bytes) as compact JSON,
except compare_laps (section 5, decision 1). The largest seen:

| Tool | Largest payload on real data |
|---|---|
| `find_session` | under 1 KB |
| `get_race_summary` | 17.3 KB (step B's sweep of every race and sprint) |
| `find_mistakes` | 13.9 KB (2024 Saudi Arabian GP R, the largest of all 263 sessions for the whole field at the default 10 rows; 19.0 KB, 2026 Canadian GP R, at the maximum of 50) |
| `explain_corner` | 27,998 B (step C2's sweep of 15 busy sessions, 137 corners); the replay drops its farthest cars to stay under 28,000 B |
| `compare_driving_styles` | 30,089 B (ALB and LEC 2024, the largest of all 1,201 driver pairs of every season; the largest teammate pair, PER and VER 2024, is 29,808 B) |
| `compare_laps` | 37-63 KB (exempt) |

Summaries are capped at 16,000 characters; the longest seen is about 6,600 (`find_mistakes`,
Monaco 2023 R, at the default 10 rows) and 11,152 at the maximum of 50 rows (2026 Canadian GP R).

**Memory.** The API process holds 531 MB after startup. After one `explain_corner` on Baku 2026
R it holds 1,043 MB, and after a second session (Monaco 2023 R) 1,531 MB: the replay keeps the
car positions of the last two sessions in memory, and the process doesn't hand back all the
memory its segment reads used. Step C2 cut the peak of an `explain_corner` on Monaco 2023 R in a fresh process from
1,552-1,588 MB to 1,377-1,399 MB by loading the session's positions once for both the traffic
explanation and the replay. Two heavy calls at a time bound the worst case. Most of what remains
is M4 code reading whole tables (`load_session_segments`, about 620 MB per call); trimming it is
deployment work for M7.

## 5. Decisions

1. **30 KiB payload budget.** Step D's open item: the HAM and LEC 2025 style payload was 30,198
   bytes, over the 30,000-byte limit the chart tests used. The budget is now 30 KiB
   (`registry.PAYLOAD_MAX_BYTES`), used by every test that checks a payload and by
   `mcp_smoke.py`. Step D swept only the 70 teammate pairs; a sweep of all 1,201 driver pairs of
   every season found the largest at 30,593 B (ALB and LEC, 2024: drivers of different teams
   carry an extra note), 127 bytes under the budget, with 227 pairs over 30,000 B. Two changes
   since: the per-event labels drop " Grand Prix" altogether ("Bahrain", under the table's
   "Event" heading), which brings the largest to 30,089 B (631 bytes to spare; teammates at most
   29,808 B), and `styles.within_budget` keeps any later season within the budget by dropping
   the per-event table's earliest rounds, with a note saying so. No real pair triggers it; a
   test runs a 24-event, 22-driver season with every number as wide as the widest real ones.
   The plan's "under 30 KB" is read as 30 KiB: 6 pairs, all of drivers of different teams in
   2024, fall between 30,000 and 30,720 bytes (the largest 30,089 B), and holding them to
   30,000 B would drop a round of their per-event table for 89 bytes.
   compare_laps is exempt: its two full-lap traces are 37-63 KB, its chart predates the budget
   and was not changed in M5.
2. **`list_sessions` stays in the chat**, so the chat, the API and Claude offer the same tools.
   `find_session` covers most of its use; the chat's system prompt points to `find_session`.
3. **`show_sample_telemetry` stays MCP-only**, as the chart pipeline's test tool.
4. **The B2 results ETL is left for later.** Step B confirmed that FastF1's cache has
   `session.results` offline (grid and classified positions, status, points) and the
   race-control messages. A `race-engineer-data results` step writing `results` and
   `race_control` tables would let `get_race_summary` give the grid, the official
   classification, penalties and retirement reasons, which it now says it can't. It was
   proposed but not built in M5, and M6 doesn't need it.
5. **Fixtures.** The race-summary `-small` fixture is the same 4-car race as the main one, for
   the whole field instead of AAA's race; W4's 20-car draft was hand-made and is gone. The
   find-mistakes `-driver` fixture comes from a copy of the data without the tracks table, which
   is how the page's no-map layout gets a fixture. In the explain-corner `-traffic` fixture AAA
   lifts less on the lap it is lapped (to 40 m/s instead of 25), so BBB is still 1.8 s up the
   road at the apex and the map draws it; before, only the replay showed a car.
6. **Stale-results cases that still answer 422.** A session processed after the scoring run,
   and a driver whose scores are stale, raise a plain input error rather than
   `results_unavailable` (step C2's open item). Both name the fix (`make m4`); they are left as
   they are.
7. **One validation for MCP, REST and the chat.** The MCP SDK validates a call against the
   function signature generated from the `Params` model, which has the same fields and limits
   but quietly drops an argument it doesn't know (`find_mistakes` with `drivers='LEC'` answered
   for the whole field) and words its errors for developers, with a pydantic docs link. The
   server (`mcp_server.server.RegistryServer`) now checks a registry tool's arguments with its
   `Params` model first, so an unknown argument is refused ("unknown parameter(s) drivers
   (parameters: event, driver, year, session, limit)") and a bad value reads as in the chat
   ("lap: must be at least 1"); its tool listing says `additionalProperties: false`, as the
   chat's tool definitions do. The REST tool routes word their 422s with the same function
   (`registry.describe_errors`, after FastAPI's "Invalid request:" prefix). Two gaps found
   later are closed. Whole-number arguments refuse true and false everywhere
   (`params.NOT_BOOL`): pydantic read true as 1, so `find_session(recent=true)` answered the
   session before last over MCP and in the chat, while REST refused it. And the REST wording
   drops only FastAPI's leading "query" from an error's location, so an argument that is
   itself called `query` (find_session's old one) is named: "unknown parameter(s) query
   (parameters: event, year, session, round, recent)".
8. **`find_session` takes fields, not a question.** It took one free-text `query`, and the
   model often passed the user's whole question. Word rules then had to tell names from the
   rest, and ordinary words kept reading as places: "Was there a safety car in the last race?"
   answered the Monaco GP ("car" as in Monte Carlo), "Tell us about the last race" the 2025
   United States GP (the pronoun "us"), "Where did Franco make mistakes in the last race?" the
   Belgian GP. Each was a wrong session given without any doubt, and three rounds of rule
   fixes each made new phrasings fail. The model reads a question better than any word list,
   so the tool now takes the fields it fills in: `event` (only the name the user gave, a
   location, country or session key, spelt right when the model recognises it), `year`,
   `session`, `round` (1-30) and `recent` (0-30; 0 is the latest). At least one of event,
   round and recent is needed. Event alone gives the newest season's qualifying, as the other
   tools do; round gives the race, of the newest processed season without a year (with an
   event, of that event's newest season); recent counts among every session unless `session`
   narrows it, so "last race" is `recent=0, session="R"`, and with `event` or `year` it counts
   only that event's or that season's sessions. Round with recent, a session key with round or
   recent, and a season or session in the event text that differs from `year` or `session`
   are one-line errors, as is giving none of the three. The tool description and every
   parameter description carry the mapping with examples ("the race before last", "round 5
   2024", "last year's Monaco race") and tell the model never to put the rest of the question
   in event. A relative year is the model's to count: from today's date when it knows it
   (Claude in the desktop app does), else from the newest processed season, which every
   answer names (and `data.coverage.newest_season`); the chat's prompt holds no date, since it
   is cached.

   A misspelt or ambiguous name gets up to five candidates, best match first, and the answer
   says to call again with the one the user plainly meant or ask them. An event with no
   session in the season asked for ("Monza" with 2021, "Bahrain" with 2026) gets its other
   seasons' sessions under "In other seasons", and the answer says to tell the user or ask
   whether another season will do, not to answer for another season unless they agree (the
   answer had told the model to call again with the candidate's event and year). An event
   whose weekend had no session of the kind asked for (a Monza sprint, "Miami" with 2022 and
   the sprint) gets the same treatment (`sessions.MissingSessionError`): only that event's
   weekends that had one as candidates (the Miami sprints of 2024-2026), never the weekend's
   qualifying, and the answer says to tell the user or ask. The chat's
   system prompt now gives the same rule: when find_session finds no single session, do what
   its answer says. Event text that isn't a name gets none: when a word of it is in no event,
   location or country name, not even misspelt (`sessions.unknown_words`), the answer names
   those words ("'was', 'there', 'a', 'safety' and 3 more are in no Grand Prix, location or
   country name, nor close to one"), a misspelt session word as one ("'sprnt' looks like
   'sprint'"), and says how to fill the fields, spelling a place the model recognises, and
   call again, never to ask the user, since the names closest to "car" or "last" (Monaco, Las
   Vegas) would be guesses. Event text with only a season or session ("race") gets the same.
   When a name without a season gave the newest season it matched and a newer weekend was held
   at the same place under another name, the answer says so: "Barcelona Spain" is the 2025
   Spanish GP, and "The 2026 Barcelona Grand Prix was also held at Barcelona". A country with
   two or three Grands Prix in a season gives the one named after it, and the answer names the
   others: "Spain" with 2026 is the Spanish GP in Madrid, "The 2026 Barcelona Grand Prix
   (Barcelona) was also held in that country"; "USA" names Miami and Las Vegas, "Italy" Imola
   (`COUNTRY_LOCATIONS`, a hand-made table: the data has no country). The `recent` description
   maps "N races ago" to recent=N-1 ("2 races ago" is the race before last), and the scripted
   chat reads it so. The question rules are gone (relative and season words, filler and
   ordinary-word lists, the prefix and near-miss rules for questions, "who won" meaning the
   race): find_session.py is 695 lines (634 at the M5 commit), about 100 of them descriptions
   and guidance the model reads, and its lookup code is about 270 lines instead of 290 plus
   120 lines of word lists. The shared error messages it needs moved to `tools/sessions.py`.
   On the real data (7,799 checks, 0 problems) every one of the 263 sessions resolves to
   itself by location, by event name and by key, and by the location of its event's other
   seasons at the same circuit (Monaco and Monte Carlo, Miami and Miami Gardens); `recent` 0 to
   3 gives list_sessions' order for each session code, each season and seven events; `round`
   gives every processed weekend of every season; 27 questions, drivers and other non-names
   put in event, each with five combinations of the other fields, get no match and no
   candidates (or a one-line error for fields that contradict); 34 misspelt names (the
   verifier's "japn", "maimi", "marina bey", "mexico cty" and "baky" among them) get the
   intended event as the first candidate or match it; 150 lookups of a name with a season it
   lacks get only other seasons' candidates and the "tell the user" answer; the newer-event
   note appears only for the Barcelona Spanish GPs, and the country note lists exactly the
   other Grands Prix of that country and season; true and false are refused. Every lookup of
   the 3-6 letter starts of every name word, alone and with "GP", with each session and season
   (9,264 lookups) either resolves to a text that fits one weekend of that season or is an
   error. The scripted chat's follow-ups use the session find_session matched (decision 12).
9. **The shared resolver** (`tools/sessions.py`, behind every tool's `event`) reads session
   codes as words of their own ("Silverstone R", "Monza Q 2025", "SQ2"), session keys
   ("2025_16_Q", as find_session returns them in `data.key`), more qualifying spellings
   ("qually", "qualies", "quals", "qualis"), possessives, and initials with or without the last
   dot or with spaces ("U.S. Grand Prix", "U.S Grand Prix", "U S Grand Prix"), and a first word
   cut to its "S" ("S. Paulo", "S Paulo", "S. Arabia"). The M5 commit had joined only fully
   dotted initials, so "U.S Grand Prix" left an "s" that read as the sprint, as 331c9c6 never
   did, and "S. Paulo" with 2024 answered for the São Paulo sprint; both resolve as at 331c9c6
   again. A location FastF1 renamed names every season of that event at the same circuit (the
   same event name and a track length within 2%): "Monte Carlo" with 2025 is the 2025 Monaco
   GP, whose location was "Monaco" until 2025, and "Miami Gardens" with 2024 the 2024 Miami
   GP; before, both said the event had no session that season. The Spanish GP's move from
   Barcelona to Madrid is a different circuit (16% longer), so "Madrid" with 2025 still finds
   nothing, and the error names the location: "Spanish Grand Prix (Madrid): no processed
   session in 2025". Text that fits several events of the season the session points to is
   ambiguous even when only one of them had that session: "Aus GP sprint" gave the 2025 US GP
   sprint (Austin) without a word about Melbourne and Spielberg, and is now "matches several
   2025 events". A single letter never matches part of a
   name ("r" is not the start of "Romagna"), a word matches the start of one only from three
   letters ("it" and "me" read as the Italian GP and Melbourne before), and inside one only
   from five, so a driver code is never part of a place: `find_mistakes(event="VER")` answered
   the British GP (Silverstone), "GAS" Las Vegas and "SAI" Lusail before; now they match
   nothing. "LV GP", which matched the "lv" inside "Silverstone" and gave the British GP, is
   Las Vegas. More circuit and country names are aliases: Catalunya, Montmeló, Paul Ricard,
   Gilles Villeneuve, Hermanos Rodríguez, Losail, Brasil, France, KSA, CDMX, USGP, San Marino
   (Imola), Baku City (Circuit), Texas, Florida and Nevada; Japan, Austria and Australia,
   already the start of their events' names, are aliases too, as whole names a misspelling is
   compared with. Suggestions ("Did you mean", and find_session's candidates) are stricter and
   come closest first: the whole text and each of its words are compared with event names,
   locations, the aliases and the names' words, never under four letters nor session words,
   and only a likeness of 0.8 or more counts. Misspelt names score 0.8 or more ("melborne"
   0.94, "vagas" 0.80) and the ordinary words of a question less (the closest, "mistakes",
   0.71 to "states"), except short words close to a short name by chance ("last" 0.86 to the
   "las" of Las Vegas), which are no longer compared; so a question no longer gets Monaco and
   Las Vegas as suggestions. One letter added, dropped, changed or swapped with the next
   counts as 0.8 even when difflib scores it lower ("maimi" 0.40 to Miami, "baky" 0.75 to
   Baku), and a name of several words counts as a whole ("mexico cty", "marina bey"), so these
   get a candidate instead of "is in no name". A tie goes to the whole name: "monze" is 0.8 to
   "monza" and to the "monte" of Monte Carlo, and lists Monza first again. A year alone that
   isn't a processed season ("2021") says so, and "fp2" as a session says practice isn't in
   the dataset. Against the M5 commit, the resolver gives the same answer for 7,309 of the
   7,744 event strings of the review's corpus (every event, location, country and
   abbreviation with each session word, with and without a year and session="R"); the other
   435 are these fixes: 339 lookups now resolve (73 U.S and U S forms, as at 331c9c6, 254 of
   the new aliases and 12 "Monte-Carlo 2022" forms); 20 "LV GP" forms give Las Vegas instead
   of the British GP; 3 "LV GP sprint" forms say Las Vegas has no sprint instead of giving the
   British GP's; 4 "Aus GP sprint" forms say they fit three events instead of giving the US
   GP's sprint; 69 errors are worded better (61 name the right event, such as "The 2026
   Barcelona Grand Prix has no processed 'sprint' session", 6 "LV GP GP" and "U.S GP GP" forms
   now suggest the right event, 2 more no longer read a sprint in "U.S"). No other lookup that
   resolved before changes. Against round 2 of these fixes, only those 12 "Monte-Carlo", 3
   "Monte-Carlo sprint" and 4 "Aus GP sprint" lookups differ.
10. **Time lost to 0.01 s in the chart data.** find_mistakes rows and explain_corner carry
   `time_lost_s` (and the phase losses) rounded once to two decimals, the precision the pages
   and the explanations show. Rounded to three first, 0.3051 s became 0.305 and the chart
   showed 0.30 s next to an explanation saying 0.31 s.
11. **Car numbers in `compare_driving_styles`.** "44" or "#16" now read as the driver who had
   that number in the season asked for (or in the newest season it was used), as the session
   tools read numbers. The style tables have codes only, so the numbers come from the
   processed laps (one DuckDB query, about 10 ms).
12. **Edits outside a step's own files.** Integration (step I) changed `tools/registry.py`
   (the budget, `PAYLOAD_MAX_BYTES`), `tools/mistake_charts.py` and its tests (C),
   `tools/styles.py` and its tests (D), `tests/test_api_tools.py` (E) and
   `tests/test_chat_runner.py` (F) to share the 30 KiB budget and the new fixtures. The fixes
   after the review touched `mcp_server/server.py`, `tools/registry.py`, `api/errors.py`,
   `tools/find_session.py` (its fields, decision 8), `tools/sessions.py` (decision 9),
   `tools/params.py` (true and false refused, decision 7), `api/chat/fake.py` (the scripted
   chat fills find_session's fields, round, "before last", "N races ago" and "last year"
   included, and runs the next tool on the session of the match's "Use event=..." line, not on
   the longest event name anywhere in the answer, which was the newest session's),
   `api/chat/prompt.py` (one rule for find_session's candidates, decision 8),
   `tools/styles.py`,
   `tools/mistakes.py`, `web/src/mcp-app/shared.ts`, `compare-laps.ts` (optional fields its
   payload already had), `find-mistakes.ts` (turn labels), their tests, the smoke scripts, the
   fixtures, the Makefile and the READMEs.

## 6. What remains for you

1. **One more chat run with a real API key.** The first run passed (section 2), but it used
   find_session's old free-text `query`. This run confirms the model fills the new fields. Start
   the API from a terminal where `ANTHROPIC_BASE_URL` is unset or is `https://api.anthropic.com`
   (the terminal Claude Code runs in sets it, and a server started there would send your key
   through it), with `ANTHROPIC_API_KEY` set or after `ant auth login`:

   ```bash
   make api                                   # /api/health should say "mode": "anthropic"
   make chat-smoke URL=http://127.0.0.1:8000  # about 10 questions, about $0.12
   ```

   Look for questions 1 and 7 calling `find_session` with `recent=0, session="R"` (or reusing
   question 1's answer), and the same results as the first run.
2. **The charts inside the Claude desktop app: done.** On 2026-10-03, at commit 7aae946, the
   reviewer ran the stdio server from the README config and asked the seven MCP questions. They
   were the most recent race you can analyse, a summary of the 2023 Monaco Grand Prix, where
   Gasly made mistakes in the 2025 Abu Dhabi race, what went wrong at the corner where he lost
   the most, where Norris gained on Piastri in 2025 Abu Dhabi qualifying, how Hamilton's style
   differs from Leclerc's, and who made the biggest mistakes in the last race. Every chart
   worked.
3. **The MCP Inspector**, if you want it on top of `make mcp-smoke`: `make mcp-inspector` prints
   the `npx @modelcontextprotocol/inspector` command and asks before downloading it.
4. **Where the code went.** Part of the shared foundation went in with M4's commit (b350b64,
   merged into `main` as PR #4):
   - `tools/cache.py`;
   - `ResultsUnavailableError` and `suggest_sessions` in `tools/sessions.py`, with their tests;
   - the synthetic tracks table and race fields in `tools/synthetic.py`;
   - `inference/style.py`'s error class;
   - compare_laps' `no_time` lap class.

   Everything else in M5 is in the M5 commit on the `m5-backend` branch, which follows the M4
   review's last answer (331c9c6).

## 7. Known limits and notes for later

- **M6:** after a `refusal` or a `retry` event mid-answer, the page should discard the text
  already streamed for that turn. The REST route of a chart tool returns the same chart data the
  MCP server sends, so the site can reuse each page's `render`.
- **M7:** the request body is parsed before the 512 KB history check, so cap the request size at
  the proxy; MCP error results can still contain absolute data paths (REST shortens them); the
  memory above; rate limits and the spending cap, which can read the per-question cost log.
- **Track maps.** `circuits.json` has 107 rounds: 106 turned as on TV, and 2026 round 14 (the
  Spanish GP at Madring) drawn north up, because its circuit info isn't in FastF1's cache. Once
  it is, `make circuits` picks it up (not `ARGS=--only-missing`, which skips rounds already in
  the file). In a tight section (Baku's castle) the find-mistakes map now moves a turn number
  with a mistake off the other markers and numbers, and leaves out the numbers without one that
  would still run together ("1110" read as one number before).
- **find_session's event text.** A few names still read as places or misspellings when put
  alone in event, against its description: "Franco" is the start of Spa-Francorchamps, and
  "Carlos", "Sainz" and "Audi" are close to Carlo, Spain and Saudi, so they get a candidate.
  A misspelling two or more edits from every name gets no candidate; the answer says the word
  is in no name nor close to one, and asks the model to spell a place it recognises. A place
  with its country matches only events whose names fit both words ("Imola Italy" is no single
  match, since Imola's event is the Emilia Romagna GP; it gets Imola and Monza as
  candidates). The countries with several Grands Prix in a season (Spain, Italy, the USA) are
  a hand-made table in find_session.py, since the data has no country; a new one needs a line
  there. A renamed location is matched to its event's other seasons by track length (within
  2%), so a Grand Prix that moved to a circuit of about the same length under the same name
  would be read as one circuit.
- `get_race_summary` copies the finish rule of `inference/traffic.chequered_lap`, and the
  fixtures import M4's test helpers through `sys.path`; both can be shared once M4's code is
  reorganised. `engine/scripts/review_queue.py` still has its own copies of the map and replay
  builders, now in `tools/track_map.py` and `tools/replay.py`.
- The REST routes keep FastAPI's "Invalid request:" prefix and code `invalid_request` for bad
  arguments (decision 7); the chat's own request body keeps pydantic's words.
