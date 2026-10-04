.PHONY: setup check lint typecheck test data qa eval-baselines train eval-deep mlflow mcp-app mcp-app-dev mcp-app-fixture mcp

ENGINE := cd engine &&
WEB := cd web &&

setup:            ## Install Python and web dependencies
	$(ENGINE) uv sync
	$(WEB) npm install

check: lint typecheck test   ## Everything CI runs

lint:
	$(ENGINE) uv run ruff check . && uv run ruff format --check .
	$(WEB) npm run lint

typecheck:
	$(ENGINE) uv run pyright
	$(WEB) npm run typecheck

test:
	$(ENGINE) uv run pytest
	$(WEB) npm test
	$(WEB) node --test "scripts/**/*.test.ts"

data:             ## Download and process sessions (resumable, paced for FastF1's API limits)
	$(ENGINE) uv run race-engineer-data build

qa:               ## Write report/data_quality.md
	$(ENGINE) uv run race-engineer-data qa

eval-baselines:   ## Run the baseline detectors and probes, write report/baselines.md
	$(ENGINE) uv run race-engineer-eval baselines

train:            ## Train the Telemetry Transformer (Mac GPU; see engine/src/race_engineer/models/train.py)
	$(ENGINE) uv run race-engineer-train

eval-deep:        ## Evaluate the newest checkpoint against the baselines -> report/m3_results.md
	$(ENGINE) uv run race-engineer-eval deep

.PHONY: experiments experiments-smoke
experiments:      ## Train and compare the M3 ablations (engine/configs/ablations.toml) -> report/m3_ablations.md
	$(ENGINE) uv run race-engineer-experiments --grid configs/ablations.toml

experiments-smoke: ## The ablation runner end to end in minutes -> report/m3_ablations_smoke.md
	$(ENGINE) uv run race-engineer-experiments --grid configs/smoke.toml

# M4 inference (engine/src/race_engineer/inference/)
.PHONY: select-model score mistakes style onnx m4
select-model:     ## Use a checkpoint for inference: CHECKPOINT=models/ablations/<run>/baseline.pt
	$(ENGINE) uv run race-engineer-infer select $(abspath $(CHECKPOINT))

score:            ## Score every corner with the selected model -> data/results/, report/m4_scoring.md
	$(ENGINE) uv run race-engineer-infer score

mistakes:         ## Type and cost every flag (after score) -> data/results/mistakes.parquet, report/m4_mistakes.md
	$(ENGINE) uv run race-engineer-infer explain

style:            ## Teammate style profiles (after score) -> data/results/style_*.parquet, report/m4_style.md
	$(ENGINE) uv run race-engineer-infer style

onnx:             ## Export the scorer for serving, parity-gated -> models/selected/mae_scorer.onnx, report/m4_onnx.md
	$(ENGINE) uv run race-engineer-infer onnx

# One step at a time even under `make -j`: each step reads what the one before it wrote.
m4:               ## Every M4 step, in order: score, mistakes, style, onnx
	$(MAKE) score
	$(MAKE) mistakes
	$(MAKE) style
	$(MAKE) onnx

.PHONY: finetune
finetune:         ## RQ3: fine-tune a temporal checkpoint on N 2026 races -> report/m3_shift.md (CHECKPOINT=...)
	$(ENGINE) uv run python -m race_engineer.models.finetune --checkpoint $(abspath $(CHECKPOINT))

# Cloud GPU (Modal; see engine/modal_app.py). Run `cd engine && uv run --group cloud modal setup` once.
.PHONY: cloud-bundle cloud-upload cloud-train cloud-fetch
BUNDLE ?= ../data/bundle
# GRID empty: configs/smoke.toml for a new run, the grid it was launched with when resuming.
GRID ?=
GPU ?= L4
CLOUD := $(ENGINE) uv run --group cloud

cloud-bundle:     ## Export the dataset as one directory for cloud training -> data/bundle
	$(ENGINE) uv run race-engineer-experiments --export-bundle $(BUNDLE)

cloud-upload:     ## Copy data/bundle to the Modal volume
	$(CLOUD) python modal_app.py upload $(BUNDLE)

cloud-train:      ## Train a grid on a Modal GPU, detached: GRID=configs/ablations.toml [GPU=A100] [ARGS=--parallel]
	$(CLOUD) modal run --detach modal_app.py $(if $(strip $(GRID)),--grid $(GRID)) --gpu $(GPU) $(ARGS)

cloud-fetch:      ## Download a cloud run into models/ablations/ and report/ (the newest, or RUN=<id>)
	$(CLOUD) python modal_app.py fetch $(if $(RUN),--run $(RUN))

mlflow:           ## Browse experiment runs at http://127.0.0.1:5000
	$(ENGINE) uv run mlflow ui --backend-store-uri sqlite:///../mlruns.db

mcp-app:          ## Build the chart bundle the MCP server serves inside Claude
	$(WEB) npm run build:mcp-app

mcp-app-dev:      ## Preview MCP App charts in a browser: open http://localhost:5174/dev-host.html
	$(WEB) npm run dev:mcp-app

mcp-app-fixture:  ## Refresh the dev host's sample results from the tools on synthetic data (see below)
	$(ENGINE) uv run python scripts/export_sample_result.py ../web/src/mcp-app
# Writes web/src/mcp-app/sample-<page>.json for telemetry, compare-laps, find-mistakes,
# explain-corner, compare-styles and race-summary, plus the variants the dev host opens with
# &fixture=: find-mistakes-{driver,empty,error}, explain-corner-{traffic,noreplay,error},
# compare-styles-{rivals,sparse,error} and race-summary-{small,error}.

mcp:              ## Run the MCP server over stdio
	$(ENGINE) uv run race-engineer-mcp

# M5 backend (engine/src/race_engineer/api/, mcp_server/). See engine/README.md.
.PHONY: api api-fake mcp-http circuits mcp-smoke chat-smoke mcp-inspector
# --port 8000 explicitly: race-engineer-api otherwise takes $PORT when it is set (the host's port).
api:              ## Run the API (tools, chat, /mcp) at http://127.0.0.1:8000, docs at /docs
	$(ENGINE) uv run race-engineer-api --reload --port 8000

# A fixed chat secret, so histories the fake chat signed stay valid across --reload restarts.
# Development only: the real API keeps a random secret per process unless one is set. The saved
# answers recorded with the scripted chat (make saved-record ... ALLOW_FAKE=1) are served too.
api-fake:         ## The API with the scripted chat instead of Claude (no API key needed)
	$(ENGINE) RACE_ENGINEER_CHAT=fake RACE_ENGINEER_CHAT_SECRET=dev-only-not-secret RACE_ENGINEER_SAVED_ALLOW_FAKE=1 \
	  uv run race-engineer-api --reload --port 8000

mcp-http:         ## Run the MCP server alone over streamable HTTP at http://127.0.0.1:8765/mcp
	$(ENGINE) uv run race-engineer-mcp --transport streamable-http

circuits:         ## Track map rotations from the FastF1 cache -> data/processed/circuits.json [ARGS=--only-missing]
	$(ENGINE) uv run race-engineer-data circuits $(ARGS)

mcp-smoke:        ## Check every MCP tool and chart on real data, time targets included: stdio, or URL=http://127.0.0.1:8000/mcp
	$(ENGINE) uv run python scripts/mcp_smoke.py --strict $(if $(URL),--url $(URL),--stdio)

chat-smoke:       ## Ask the chat the example questions: in-process scripted, or URL=http://127.0.0.1:8000
	$(ENGINE) uv run python scripts/chat_smoke.py --strict $(if $(URL),--url $(URL),--app)

INSPECTOR := npx @modelcontextprotocol/inspector uv --directory engine run race-engineer-mcp
mcp-inspector:    ## Open the MCP Inspector on the stdio server (npx downloads it; asks first)
	@echo "This downloads the MCP Inspector from npm and runs it on the stdio server:"
	@echo "  $(INSPECTOR)"
	@printf 'Download and run it? [y/N] '; read answer; \
	if [ "$$answer" = y ] || [ "$$answer" = Y ]; then $(INSPECTOR); else echo "Not run."; fi

# M6 website (web/). See web/README.md.
.PHONY: web web-test site-smoke site-snapshots
web:              ## Run the website at http://localhost:3000 (start `make api-fake` or `make api` for its data)
	$(WEB) npm run dev

web-test:         ## The website's unit tests (Node's built-in runner, no browser)
	$(WEB) npm test

# site-smoke needs the site and the API running (make web, make api-fake) and writes nothing;
# site-snapshots needs the API. Defaults: SITE=http://localhost:3000, API=http://127.0.0.1:8000.
# ORIGIN checks CORS for that origin alone (a deployed API's); CHAT=skip asks nothing, CHAT=one one
# question of any chat (the default, fake, asks 17 of the scripted chat).
site-smoke:       ## Check the running site and API end to end: pages, tools, chat, CORS [SITE=... API=... ORIGIN=... CHAT=...]
	$(WEB) npm run smoke -- $(if $(SITE),--site $(SITE)) $(if $(API),--api $(API)) $(if $(ORIGIN),--origin $(ORIGIN)) $(if $(CHAT),--chat $(CHAT))

site-snapshots:   ## Save the featured corners from a running API -> web/public/snapshots/ (gitignored) [API=...]
	$(WEB) node scripts/snapshots.ts $(if $(API),--api $(API))

# M7: the production-like local profile, the guardrail and accuracy checks, the saved answers and
# the deploy (docs/DEPLOY.md has the whole runbook). serve-upload, deploy-api, deploy-check,
# limits-check and chat-pause/-resume/-status act on your Modal account, your Upstash database or
# the live site: run them from your own terminal, with ANTHROPIC_BASE_URL unset.
.PHONY: api-prodlike web-prodlike limits-burst limits-check chat-pause chat-resume chat-status \
	chat-eval saved-record serve-data serve-upload deploy-api deploy-check serve-venv-check budgets \
	demo-video

# The deployed configuration minus Modal, Upstash and the real model (M7 plan 14.3): the served-data
# bundle (make serve-data ALLOW_FAKE=1), a public RACE_ENGINEER_HOST while listening on 127.0.0.1
# only, stateless /mcp, the limits on with the memory store (a restart clears it), the scripted
# chat with its notional cost counted, and the scripted saved answers. uvicorn directly, because
# race-engineer-api's --host would overwrite RACE_ENGINEER_HOST; --no-proxy-headers, so a client
# can't choose its own visitor code with X-Forwarded-For. The browser origin is web-prodlike's.
api-prodlike:     ## The API as deployed, on http://127.0.0.1:8001: scripted chat, limits on [RATE_HOUR=3 CAP=0.01 PAUSED=1 CHAT=off]
	@case "$(or $(CHAT),fake)" in fake|off) ;; *) echo "api-prodlike: CHAT is fake or off (the real chat runs with make api)"; exit 2;; esac
	@test -d data/serve || { echo "api-prodlike: no data/serve; run make serve-data ALLOW_FAKE=1 first"; exit 1; }
	$(ENGINE) RACE_ENGINEER_DATA=../data/serve RACE_ENGINEER_HOST=0.0.0.0 RACE_ENGINEER_MCP_STATELESS=1 \
	  RACE_ENGINEER_CHAT=$(or $(CHAT),fake) RACE_ENGINEER_CHAT_SECRET=dev-only-not-secret \
	  RACE_ENGINEER_LIMITS=on RACE_ENGINEER_LIMITS_STORE=memory RACE_ENGINEER_LIMITS_COUNT_FAKE=1 \
	  RACE_ENGINEER_RATE_HOUR=$(or $(RATE_HOUR),10) RACE_ENGINEER_DAILY_CAP_USD=$(or $(CAP),3.00) \
	  RACE_ENGINEER_PAUSED=$(or $(PAUSED),0) RACE_ENGINEER_SAVED_ALLOW_FAKE=1 \
	  RACE_ENGINEER_CORS_ORIGINS=http://localhost:3001,http://127.0.0.1:3001 \
	  uv run uvicorn race_engineer.api.app:create_app --factory --host 127.0.0.1 --port 8001 \
	  --no-access-log --no-proxy-headers

# A production build calling api-prodlike, served by `next start`. Not while `next dev` runs on
# web/ (make web): both use web/.next (M6 decision 18).
web-prodlike:     ## The site's production build on http://localhost:3001, calling api-prodlike (stop make web first)
	@if [ -f web/.next/dev/lock ] && kill -0 "$$(sed -n 's/.*"pid": *\([0-9]*\).*/\1/p' web/.next/dev/lock)" 2>/dev/null; then \
	  echo "web-prodlike: next dev is running on web/ (web/.next/dev/lock); stop make web first"; exit 1; fi
	$(WEB) NEXT_PUBLIC_API_URL=http://127.0.0.1:8001 npm run build
	$(WEB) npm run start -- --port 3001

limits-burst:     ## N scripted questions from one client: 10 answers, then 429 (URL=http://127.0.0.1:8001 [N=12] [ORIGIN=...])
	$(ENGINE) uv run python scripts/limits_burst.py --url $(or $(URL),$(error limits-burst needs URL=, e.g. URL=http://127.0.0.1:8001)) \
	  -n $(or $(N),12) $(if $(ORIGIN),--origin $(ORIGIN))

# The limits on Upstash, read from UPSTASH_REDIS_REST_URL and UPSTASH_REDIS_REST_TOKEN in your shell.
limits-check:     ## Upstash: the limits store's contract on your database (writes, then deletes, re:test: keys)
	$(ENGINE) uv run python scripts/limits_admin.py check

chat-pause:       ## Upstash: pause the live chat (the site shows saved answers)
	$(ENGINE) uv run python scripts/limits_admin.py pause

chat-resume:      ## Upstash: resume the live chat
	$(ENGINE) uv run python scripts/limits_admin.py resume

chat-status:      ## Upstash: today's questions, spend, reservations and the switch
	$(ENGINE) uv run python scripts/limits_admin.py status

# In-process it always uses the scripted chat and writes data/evals/; with URL= it asks that API,
# and a run against Claude (an API started with your key) writes report/m7_chat_accuracy.md.
chat-eval:        ## The chat's 15 accuracy questions: in-process scripted, or URL=http://127.0.0.1:8000 [REPEAT=3]
	$(ENGINE) uv run python scripts/chat_eval.py --strict $(if $(URL),--url $(URL),--app) $(if $(REPEAT),--repeat $(REPEAT))

# From web/, so ../data/saved is the repository's data/saved (gitignored). Recording the scripted
# chat needs ALLOW_FAKE=1; Claude's seven answers cost about $0.05-0.15.
saved-record:     ## Record the chat's 7 saved answers from API= into data/saved/ [ALLOW_FAKE=1]
	$(WEB) node scripts/saved.ts record --api $(or $(API),$(error saved-record needs API=, e.g. API=http://127.0.0.1:8000)) \
	  --out ../data/saved $(if $(ALLOW_FAKE),--allow-fake)

serve-data:       ## Build data/serve, the files the deployed API reads (hard links), and check it [ALLOW_FAKE=1]
	$(if $(TRIM),$(error TRIM: serve_data.py has no --trim yet (plan 6.3, for the Cloud Run fallback)))
	$(ENGINE) uv run python scripts/serve_data.py --out ../data/serve $(if $(ALLOW_FAKE),--allow-fake)

serve-upload:     ## Your step: copy data/serve to the Modal volume race-engineer-serve (creates it if missing)
	$(ENGINE) uv run --group cloud python modal_api.py upload ../data/serve

# $(value ...): a regex's $ and backslashes reach modal_api.py as typed.
deploy-api:       ## Your step: deploy the API to Modal [SITE_ORIGIN=https://<project>.vercel.app] [PREVIEW_REGEX=...]
	$(MAKE) mcp-app
	$(ENGINE) SITE_ORIGIN='$(value SITE_ORIGIN)' PREVIEW_REGEX='$(value PREVIEW_REGEX)' uv run --group cloud modal deploy modal_api.py

# Every part runs even when one fails. CHAT=one (the default) asks the live chat one question
# (about $0.01) to check that answers stream through Modal; CHAT=skip asks nothing.
deploy-check:     ## The live site and API: pages, saved answers, CSP, CORS, /mcp, limits, client address (SITE= API= [CHAT=skip])
	@test -n "$(SITE)" && test -n "$(API)" || { echo "deploy-check needs SITE=https://<project>.vercel.app API=https://<workspace>--race-engineer-api.modal.run"; exit 2; }
	status=0; \
	(cd web && node scripts/site-smoke.ts --site $(patsubst %/,%,$(SITE)) --api $(patsubst %/,%,$(API)) \
	  --origin $(patsubst %/,%,$(SITE)) --chat $(or $(CHAT),one)) || status=1; \
	(cd engine && uv run python scripts/mcp_smoke.py --url $(patsubst %/,%,$(API))/mcp) || status=1; \
	(cd engine && uv run python scripts/serve_check.py --remote $(patsubst %/,%,$(API)) --origin $(patsubst %/,%,$(SITE))) || status=1; \
	exit $$status

# A fresh serve-only install (never engine/.venv, which every other command shares), checked with
# its own Python: `uv run` would first sync the default groups, training included, into it.
serve-venv-check: ## The API's serve-only install (data/serve-venv): no training packages, and it starts
	cd engine && UV_PROJECT_ENVIRONMENT=../data/serve-venv uv sync --locked --no-default-groups && \
	  ../data/serve-venv/bin/python scripts/serve_check.py --local

budgets:          ## Initial JavaScript per page against the size limits (after a production build in web/)
	npm --prefix web run budgets

demo-video:       ## The demo video -> data/demo/race-engineer-demo.mp4 [SITE=... API=...] (a real chat: one question, ~$0.02)
	$(WEB) node scripts/demo/record.ts $(if $(SITE),--site $(SITE)) $(if $(API),--api $(API))
