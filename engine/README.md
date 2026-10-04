# race-engineer (engine)

The Python side of Race Engineer AI: data pipeline, models, analysis tools, API and MCP server.
See the [project README](../README.md) for the overview, and [docs/DEPLOY.md](../docs/DEPLOY.md)
for deploying the API.

```bash
uv sync                      # install (the dev and train groups, below)
uv run pytest                # tests (synthetic data: no dataset, network or API key needed)
uv run race-engineer-mcp     # MCP server over stdio
uv run race-engineer-api     # API at http://127.0.0.1:8000 (tools, chat, /mcp), docs at /docs
```

The commands, each behind a `make` target in the repository's Makefile: `race-engineer-data`
(download, process and check the data: `make data`, `make qa`, `make circuits`),
`race-engineer-eval` (`make eval-baselines`, `make eval-deep`), `race-engineer-train` and
`race-engineer-experiments` (`make train`, `make experiments`), `race-engineer-infer`
(`make select-model`, `make m4`), `race-engineer-api` (`make api`, `make api-fake`) and
`race-engineer-mcp` (`make mcp`, `make mcp-http`).

## Dependency groups

The project's own dependencies are only what serving needs: the API, every tool, the chat, the
MCP server (anthropic, duckdb, fastapi, httpx2, mcp, numpy, pandas, pyarrow, scikit-learn, scipy,
uvicorn). The rest are groups:

| Group | Holds | Installed by |
|---|---|---|
| `train` | torch, MLflow, FastF1, matplotlib, ONNX Runtime: the data pipeline, training, scoring | `uv sync` (a default group) |
| `dev` | pytest, ruff, pyright, the ONNX exporter, notebooks | `uv sync` (a default group) |
| `cloud` | the Modal client | `uv run --group cloud …` |

So `uv sync`, `make check` and CI install everything, as before. A **serve-only install** (what
the deployed API runs: no torch, no CUDA wheels, no MLflow) is
`uv sync --locked --no-default-groups`, always into a fresh environment named by
`UV_PROJECT_ENVIRONMENT`, never into `engine/.venv`, where it would uninstall the training and
test packages:

```bash
UV_PROJECT_ENVIRONMENT=../data/serve-venv uv sync --locked --no-default-groups
../data/serve-venv/bin/python scripts/serve_check.py --local     # make serve-venv-check does both
```

`tests/test_dependency_split.py` (in CI) checks that the serve set's closure in `uv.lock` holds no
training or CUDA package, and that importing the app, the tool definitions and the MCP server
loads none of them.

## Analysis tools

Each tool is a `ToolSpec` (`race_engineer/tools/registry.py`): a name, a description for the
model, a pydantic `Params` model for its inputs, and a `run` that returns a `ToolOutput` with a
`summary` (the text the model reads) and `data` (the chart payload, which never goes back to the
model). `tools/definitions.py` lists them in a fixed order, `TOOLS`, which the MCP server, the
REST routes and the chat all build from, so a tool answers the same everywhere. `run_tool`
validates the arguments, runs the tool and caches the result until the data changes
(`data_version`); `run_tool_async` runs it in a worker thread, with at most two heavy tools
(explain_corner, compare_laps) at once, and refuses a heavy call as 503 `busy` when more than 8
are already waiting (`RACE_ENGINEER_HEAVY_QUEUE`).

| Tool | Module | Chart bundle |
|---|---|---|
| `find_session` | `tools/find_session.py` | — |
| `list_sessions` | `tools/definitions.py` | — |
| `get_race_summary` | `tools/race_summary.py` | `race-summary` |
| `find_mistakes` | `tools/mistakes.py` (track map: `tools/track_map.py`) | `find-mistakes` |
| `explain_corner` | `tools/mistakes.py` (map and replay: `tools/mistake_charts.py`, `tools/replay.py`) | `explain-corner` |
| `compare_laps` | `tools/lap_comparison.py` | `compare-laps` |
| `compare_driving_styles` | `tools/styles.py` | `compare-styles` |

Errors a user can act on are `ToolInputError` (an unknown event or driver, a lap that doesn't
exist), relayed as they are; `ResultsUnavailableError` means the M4 results are missing or out
of date (`make m4`). A chart's data stays under 30 KiB as compact JSON
(`registry.PAYLOAD_MAX_BYTES`), except compare_laps' two full-lap traces.

The chart bundles are built from `web/src/mcp-app/` into `race_engineer/mcp_server/ui/` by
`make mcp-app`. `scripts/export_sample_result.py` (`make mcp-app-fixture`) writes the dev host's
sample results by running the tools on synthetic data.

The track maps are drawn north up unless `make circuits` has written the circuits' rotations from
the FastF1 cache to `data/processed/circuits.json` (run it again with `ARGS=--only-missing` after
new rounds).

## API (`race_engineer/api/`)

`race-engineer-api` (`api/main.py`) runs `api/app.py:create_app` with uvicorn: `--host`
(default 127.0.0.1), `--port` (default `$PORT` when it is set, else 8000), `--reload`, and
`--no-access-log` (uvicorn's access log prints every client's address; the Docker image turns it
off, and Modal serves the app without uvicorn). At startup it loads the session catalog and the style tables (`registry.warm`), starts the
chat client and the MCP session manager; missing data never stops it, and `/api/health` says what
is missing.

| Path | What |
|---|---|
| `GET /api/health` | data, results, chat and MCP status: always 200, `status` `ok` or `degraded` (the data). Also `chat.available` and `chat.reason`, and `limits` (`enabled`, `store`, `per_hour`, `per_day`, `problems`); never a dollar amount |
| `GET /api/ready` | 200 once the warm-up has finished and the data has no problem, else 503 (for container hosts' startup probes) |
| `GET /api/tools`, `GET /api/tools/<name>?…` | the tool catalog, and one tool with its arguments as query parameters, e.g. `/api/tools/find_mistakes?event=baku&year=2026&session=R`; with ETags |
| `GET /api/catalog/sessions`, `/drivers`, `/styles` | the website's pickers (below) |
| `POST /api/chat` | the chat, streamed as server-sent events (`{"message": "..."}`) |
| `GET /api/chat/status` | whether a question would be admitted now, and the visitor's quota; it reads the limits but never counts |
| `GET /api/saved`, `GET /api/saved/<id>` | the saved example answers (below) |
| `/mcp` | the MCP server over streamable HTTP, with the same tools and charts as over stdio |

- Errors are JSON `{"error": {"code", "message", …}}`: 422 `invalid_request` or `invalid_input`,
  503 `results_unavailable`, 404 `unknown_tool`, 500 `internal_error` (`api/errors.py`), plus
  the chat's refusals (below), 429 `rate_limited` and 503 `busy` on the tools.
- The catalog routes (`api/routes/catalog.py`) list only what the tools would accept:

  | Route | Answers |
  |---|---|
  | `GET /api/catalog/sessions` | every processed session by season and race weekend (both newest first), each with whether the M4 scores cover it, and whether its season is in the driving-style tables |
  | `GET /api/catalog/drivers?event=...&year=...&session=...` | one session's drivers, with their team and laps in the data |
  | `GET /api/catalog/styles?year=...` | one season's drivers and teammate pairs in the driving-style tables (default: the newest season), each pair once |

  They are read-only and not tools: they aren't `ToolSpec`s, so the MCP server, the chat's tool
  list and its prompt version (which signs the histories browsers hold) stay as they are. Errors
  are the tools' (422 for an unknown event, season or query key, 503 when the data needs
  rebuilding), and each answer has an ETag with `Cache-Control: private, max-age=300`. Tests:
  `tests/test_api_catalog.py`.
- Middleware, outermost first: CORS (exact origins from `RACE_ENGINEER_CORS_ORIGINS`, or `none`;
  `RACE_ENGINEER_CORS_ORIGIN_REGEX` for preview deployments; `Retry-After` exposed), so every
  refusal carries the CORS headers; gzip for JSON of 1 KB or more (never the chat's event
  stream); the request rates; the body limit; then the app's own error handler.
- **Settings** are environment variables, all optional, documented in `api/settings.py`; a bad
  value stops startup with a message naming the variable. The guardrails' settings and defaults
  are also tabled in [report/m7_ship.md](../report/m7_ship.md#settings).

## Guardrails (`race_engineer/api/limits/`)

With a real model the chat costs money, so a deployed chat has limits. They are on when
`RACE_ENGINEER_LIMITS` is `on`, or `auto` (the default) with the real model and a host other than
loopback: exactly a deployment, never a local `make api`.

- **Per visitor:** 10 questions in a rolling hour and 25 a UTC day (`RACE_ENGINEER_RATE_HOUR`,
  `_RATE_DAY`). A visitor is `identity.py`'s code: an HMAC of the IPv4 address or IPv6 /64, salted
  with a key derived from `RACE_ENGINEER_CHAT_SECRET` and the UTC day. No address, cookie or
  question is stored; the counters expire within about a day. Behind proxies,
  `RACE_ENGINEER_TRUSTED_PROXY_HOPS` says how many `X-Forwarded-For` entries to trust, from the
  right. On a public host, a request with no client address is refused rather than counted in a
  shared bucket.
- **Money:** $3 a day in all (`RACE_ENGINEER_DAILY_CAP_USD`; 0 closes the chat), from the chat's
  own token accounting (`chat/pricing.py`). Admission reserves $0.10 per question in flight
  (`_RESERVE_USD`); `routes/chat.py`'s `accept()` settles each admitted question exactly once,
  after its stream closes, charging what it cost and refunding the question when the visitor got
  nothing through no fault of theirs (the client left before the answer started, Claude was busy
  or unreachable). A question stops before its next call to the model once it has cost $0.30
  (`RACE_ENGINEER_CHAT_MAX_COST_USD`, stream error `too_costly`), and 1,000 questions a day is the
  backstop (`_DAILY_QUESTIONS`). The scripted chat's notional cost isn't counted unless
  `RACE_ENGINEER_LIMITS_COUNT_FAKE=1`.
- **The store:** the counters are Redis keys under `re:v1:`, changed in one transaction per
  admission and per settle (`store.py`), in memory (`memory.py`: local runs and tests, reset on
  restart) or in Upstash Redis over its REST API (`upstash.py`: the deployment, no retries, a 2 s
  timeout, the URL and token never logged). `RACE_ENGINEER_LIMITS_STORE` picks it (`auto`: Upstash
  when `UPSTASH_REDIS_REST_URL` and `UPSTASH_REDIS_REST_TOKEN` are set). **The limits fail
  closed:** a store that is missing or unreachable turns the chat off (503 `chat_unavailable`)
  while the tools, the catalog and `/mcp` keep working.
- **Keeping traffic off the store:** request rates in process (`http.py`: 120 a minute per
  visitor and 600 in all on the tools, the catalog and the saved answers; 20 and 120 on
  `POST /api/chat`; 60 per visitor on the chat status; 240 in all on `/mcp`), refusals remembered
  until they expire, the status cached 10 s per visitor and health's global part 60 s.
- **Request sizes:** 640 KiB on `POST /api/chat`, answered 413 `request_too_large` before the
  body is parsed (`RACE_ENGINEER_CHAT_MAX_BODY`), and 512 KiB on `/mcp`, the MCP SDK's own limit
  (`RACE_ENGINEER_MCP_MAX_BODY`).
- **The kill switch:** the store's `re:v1:kill` key, or `RACE_ENGINEER_PAUSED=1`.
  `scripts/limits_admin.py` (`make chat-pause`, `chat-resume`, `chat-status`, `limits-check`)
  works the real store from your terminal; `check` runs the store's contract cases (`contract.py`,
  the same as `tests/test_limits_store.py`) on it under a throwaway prefix.

The chat's refusals before a stream starts, with `retry_after_s` in the body and a `Retry-After`
header:

| Status | Code | When |
|---|---|---|
| 429 | `rate_limited` (`limit`: `hour` or `day`) | the visitor's 10 an hour or 25 today are used |
| 503 | `daily_cap` | the day's spend and reservations would pass the cap, or the backstop is reached |
| 503 | `chat_paused` | the kill switch is on |
| 503 | `chat_unavailable` | the store is unconfigured or unreachable, or the client address is missing on a public host |
| 413 | `request_too_large` | the body is over 640 KiB |

Tests: `test_limits_identity.py`, `test_limits_store.py` (one contract on both stores, the
Upstash one over `fake_upstash.py`), `test_api_limits.py` (through the app).

## Saved example answers

`GET /api/saved` lists the recordings in `RACE_ENGINEER_SAVED` (default `$RACE_ENGINEER_DATA/saved`)
and `GET /api/saved/<id>` serves one byte for byte (`routes/saved.py`). They are written by
`web/scripts/saved.ts record` (`make saved-record API=…`), one per example question of the chat
page, and the site shows them whenever the live chat is limited, paused or off. Recordings made
with the scripted chat are hidden unless `RACE_ENGINEER_SAVED_ALLOW_FAKE=1` (the local profiles
only), so a deployment never offers a scripted answer as Claude's. `data/saved/` is never
committed: the charts in a recording carry positions derived from F1 data.

## Chat (`race_engineer/api/chat/`)

Claude Sonnet 5.5 through the Anthropic SDK's tool runner (`runner.py`), with the tools built
from the same specs (`tools.py`) and a system prompt (`prompt.py`) cached with the tool list.
The server stores no transcripts: the history comes back from the browser, signed with an HMAC
(`session.py`), at most 8 questions per conversation and 4 chats at once per process. Each
question logs its tools, tokens, cost and the visitor's questions left (`quota=`), never its text
or the visitor.

- *Scripted mode, no API key:* `RACE_ENGINEER_CHAT=fake` (`make api-fake`) replaces Claude with a
  scripted stand-in (`fake.py`) that streams real Anthropic events through the real SDK and calls
  the real tools, so the whole pipeline runs without a key; the tests use it too.
  `make chat-smoke` asks the example questions in-process; with `make api-fake` running,
  `make chat-smoke URL=http://127.0.0.1:8000` asks them over HTTP.
- *Real mode:* needs your own Anthropic API key (`ANTHROPIC_API_KEY`, or `ant auth login`). Start
  `make api` from a shell where `ANTHROPIC_BASE_URL` is unset or is the Claude API itself
  (`https://api.anthropic.com`): any other base URL would see every question and the key, and the
  API warns about it at startup. `/api/health` then reports `"mode": "anthropic"`.

## Chat accuracy checks (`race_engineer/chat_eval/`)

Fifteen fixed questions in two conversations (`evals/chat_questions.toml`), each with the tools
it should call (checked only when they answered without an error), checks on their arguments as
the tool resolved them, tools it must not call, and named behaviour checks (declining to predict,
not leaking the system prompt, saying when nothing was flagged). A grounding check reads every
number in an answer: it must come from a tool summary the model saw, a question, the system
prompt or the tool descriptions (`grounding.py`).

```bash
make chat-eval                              # in-process, scripted chat -> data/evals/chat_eval-fake-<time>.md
make chat-eval URL=http://127.0.0.1:8000    # a running API; with Claude, report/m7_chat_accuracy.md
```

The scripted chat tests the harness, not the chat (it picks tools by word rules), and is never
written into `report/`. A run whose every answer came from Claude writes
`report/m7_chat_accuracy.md` and a JSON of summaries, inputs and checks under `data/evals/`; with
`--strict` (as `make chat-eval` runs it) it exits 1 when a question failed a check, after both are
written. Tests: `tests/test_chat_eval.py`.

## MCP server (`race_engineer/mcp_server/`)

The same tools and charts over stdio for the Claude desktop app (`make mcp`), inside the API at
`/mcp`, or alone over HTTP (`make mcp-http`, http://127.0.0.1:8765/mcp). Every tool is read-only,
with validated arguments; error results never carry absolute data paths.
`RACE_ENGINEER_MCP_STATELESS=1` (the deployments) serves `/mcp` without sessions, one JSON
response per request, so a container restart breaks nothing; that is what Claude's custom
connector calls. While bound to localhost, `/mcp` also refuses other `Host` headers (the MCP SDK's
DNS-rebinding protection, `RACE_ENGINEER_MCP_HOST_CHECK`).

`make mcp-smoke` starts the server over stdio and checks every tool and chart bundle on real data,
the way an MCP Inspector session would, with the time targets (explain_corner under 2 s cold, the
other tools under 1 s); `make mcp-smoke URL=http://127.0.0.1:8000/mcp` checks the one inside a
running API. `make mcp-inspector` prints the `npx @modelcontextprotocol/inspector` command and
asks before downloading it.

## Scripts (`engine/scripts/`)

| Script | What | Make target |
|---|---|---|
| `mcp_smoke.py` | every MCP tool and chart over stdio or a URL, with time targets | `make mcp-smoke [URL=]` |
| `chat_smoke.py` | the chat's example questions, in-process or over HTTP | `make chat-smoke [URL=]` |
| `chat_eval.py` | the 15 accuracy questions (above) | `make chat-eval [URL=] [REPEAT=]` |
| `serve_data.py` | the served-data bundle: hard links to the files the API reads, the saved answers, a manifest, checked against the source data's health | `make serve-data [ALLOW_FAKE=1]` |
| `serve_check.py` | `--local`: a serve-only install has no training package and serves; `--remote`: a deployment's health, CORS, gzip, saved answers, client address and timings | `make serve-venv-check`, part of `make deploy-check` |
| `limits_admin.py` | the kill switch and the store's contract on the real Upstash database | `make chat-pause`, `chat-resume`, `chat-status`, `limits-check` |
| `limits_burst.py` | a burst of scripted questions from one client must end in 429 (refuses a real chat) | `make limits-burst URL= [N=12]` |
| `export_sample_result.py` | the MCP App dev host's sample results, from synthetic data | `make mcp-app-fixture` |
| `synthetic_tree.py` | a made-up data tree the API serves with health ok, for CI's end-to-end check | — (CI) |
| `review_queue.py` | the M4 review queue | — |

Figures: `scripts/plot_style_map.py`, `plot_reconstruction.py` and `probe_within_season.py` take
a checkpoint path.

## Deploying the API

The steps are in [docs/DEPLOY.md](../docs/DEPLOY.md). The files:

- **`modal_api.py`** is the Modal app `race-engineer-api`: the serve-only dependencies, the source
  with the chart bundles, the served data on the volume `race-engineer-serve` at `/data`
  (read-only), the secret `race-engineer-api`, one container that scales to zero, and an explicit
  environment (a public host, stateless MCP, the limits on with Upstash, CORS from `SITE_ORIGIN`).
  `python modal_api.py upload [../data/serve] [--dry-run]` copies a bundle to the volume
  (`make serve-upload`); `modal deploy modal_api.py` deploys it (`make deploy-api`), refusing while
  `ANTHROPIC_BASE_URL` is set or a chart bundle is missing.
- **`Dockerfile`** is the fallback image (Cloud Run or any container host), built from the
  repository root (`docker build -f engine/Dockerfile .`): the chart bundles built in a Node stage,
  the serve-only install, no data (it is mounted at `/data`), a non-root user, `/api/ready` as its
  health check.
- **`modal_app.py`** is the other Modal app, for training (below).

## Training in the cloud (Modal)

The M3 ablation grid takes about 10 hours on a laptop GPU. `modal_app.py` runs it on a
[Modal](https://modal.com) GPU (an L4 by default) instead, with the `train` group installed in its
image. Training reads a single exported bundle rather than the processed Parquet tree. The bundle
and all run outputs live on a volume in your own Modal workspace, and results come back as
checkpoints, a report and JSON.

```bash
cd engine && uv run --group cloud modal setup && cd ..   # once: log in to your Modal account
make cloud-bundle      # export the dataset -> data/bundle (~1.2 kB per segment)
make cloud-upload      # copy it to the Modal volume "race-engineer"
make cloud-train       # configs/smoke.toml first: checks the image, GPU and bundle in minutes
make cloud-train GRID=configs/ablations.toml                 # variants in turn on one GPU
make cloud-train GRID=configs/ablations.toml ARGS=--parallel # one GPU per variant
make cloud-fetch       # newest run -> models/ablations/<run>/, report/, local MLflow
```

`cloud-train` runs detached, so closing the terminal doesn't stop it. It prints a run id;
`make cloud-fetch RUN=<id>` downloads whichever variants have finished so far. Other options are
`GPU=A100`, `ARGS="--only baseline,cnn --hours 6"`, and `ARGS="--run-id <id>"`, which resumes a
run on the volume with the grid it was launched with (no `GRID` needed; a different one is
refused) and skips the variants that already finished. Cloud runs use TF32 matmuls, so compare
cloud variants with each other rather than with Mac runs.

The full grid trains on 2022-2024, validates on 2025 and tests on 2026 (`split = "temporal"`; the
smoke grid uses `"by_event"`, which works on any subset of the data). A grid refuses to train on a
split whose training part holds under 40% of the segments (`min_train_share`). With one L4 per
variant the grid takes about 20 minutes and a few dollars; the CNN variant is much slower on CUDA
than the Transformer (about 8 minutes an epoch), which is still to be profiled.

## Checks on real data (not in CI)

```bash
uv run python scripts/mcp_smoke.py --strict            # every MCP tool and chart over stdio
uv run python scripts/mcp_smoke.py --strict --url http://127.0.0.1:8000/mcp
uv run python scripts/chat_smoke.py --strict           # the example questions, scripted chat
uv run python scripts/chat_smoke.py --strict --url http://127.0.0.1:8000
uv run python scripts/chat_eval.py --strict            # the 15 accuracy questions, scripted chat
```
